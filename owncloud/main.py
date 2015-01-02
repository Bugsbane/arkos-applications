import errno
import nginx
import os
import shutil

from arkos.core.languages import php
from arkos.core.sites import SiteEngine
from arkos.core.utilities import shell, random_string


class ownCloud(SiteEngine):
    addtoblock = [
        nginx.Key('error_page', '403 = /core/templates/403.php'),
        nginx.Key('error_page', '404 = /core/templates/404.php'),
        nginx.Key('client_max_body_size', '10G'),
        nginx.Key('fastcgi_buffers', '64 4K'),
        nginx.Key('rewrite', '^/caldav(.*)$ /remote.php/caldav$1 redirect'),
        nginx.Key('rewrite', '^/carddav(.*)$ /remote.php/carddav$1 redirect'),
        nginx.Key('rewrite', '^/webdav(.*)$ /remote.php/webdav$1 redirect'),
        nginx.Location('= /robots.txt',
            nginx.Key('allow', 'all'),
            nginx.Key('log_not_found', 'off'),
            nginx.Key('access_log', 'off')
            ),
        nginx.Location('~ ^/(?:\.htaccess|data|config|db_structure\.xml|README)',
            nginx.Key('deny', 'all')
            ),
        nginx.Location('/',
            nginx.Key('rewrite', '^/.well-known/host-meta /public.php?service=host-meta last'),
            nginx.Key('rewrite', '^/.well-known/host-meta.json /public.php?service=host-meta-json last'),
            nginx.Key('rewrite', '^/.well-known/carddav /remote.php/carddav/ redirect'),
            nginx.Key('rewrite', '^/.well-known/caldav /remote.php/caldav/ redirect'),
            nginx.Key('rewrite', '^(/core/doc/[^\/]+/)$ $1/index.html'),
            nginx.Key('try_files', '$uri $uri/ index.php')
            ),
        nginx.Location('~ \.php(?:$|/)',
            nginx.Key('fastcgi_split_path_info', '^(.+\.php)(/.+)$'),
            nginx.Key('include', 'fastcgi_params'),
            nginx.Key('fastcgi_param', 'SCRIPT_FILENAME $document_root$fastcgi_script_name'),
            nginx.Key('fastcgi_param', 'PATH_INFO $fastcgi_path_info'),
            nginx.Key('fastcgi_pass', 'unix:/run/php-fpm/php-fpm.sock'),
            nginx.Key('fastcgi_read_timeout', '900s')
            ),
        nginx.Location('~* \.(?:jpg|jpeg|gif|bmp|ico|png|css|js|swf)$',
            nginx.Key('expires', '30d'),
            nginx.Key('access_log', 'off')
            )
        ]

    def pre_install(self, name, vars):
        if vars.getvalue('oc-username', '') == '':
            raise Exception('Must choose an ownCloud username')
        elif vars.getvalue('oc-logpasswd', '') == '':
            raise Exception('Must choose an ownCloud password')
        elif '"' in vars.getvalue('oc-logpasswd', '') or "'" in vars.getvalue('oc-logpasswd', ''):
            raise Exception('Your ownCloud password must not include quotes')

    def post_install(self, name, path, vars, dbinfo={}):
        datadir = ''
        secret_key = random_string()
        username = vars.getvalue('oc-username')
        logpasswd = vars.getvalue('oc-logpasswd')

        # Set ownership as necessary
        if not os.path.exists(os.path.join(path, 'data')):
            os.makedirs(os.path.join(path, 'data'))
        uid, gid = self.System.Users.get("http")["uid"], self.System.Users.get_group("http")["gid"]
        for r, d, f in os.walk(path):
            for x in d:
                os.chown(os.path.join(root, x), uid, gid)
            for x in f:
                os.chown(os.path.join(root, x), uid, gid)

        # If there is a custom path for the data directory, do the magic
        if vars.getvalue('oc-ddir', '') != '':
            datadir = vars.getvalue('oc-ddir')
            if not os.path.exists(os.path.join(datadir if datadir else path, 'data')):
                os.makedirs(os.path.join(datadir if datadir else path, 'data'))
            for r, d, f in os.walk(os.path.join(datadir if datadir else path, 'data')):
                for x in d:
                    os.chown(os.path.join(root, x), uid, gid)
                for x in f:
                    os.chown(os.path.join(root, x), uid, gid)
            php.open_basedir('add', datadir)

        # Create ownCloud automatic configuration file
        with open(os.path.join(path, 'config', 'autoconfig.php'), 'w') as f:
            f.write(
                '<?php\n'
                '   $AUTOCONFIG = array(\n'
                '   "adminlogin" => "'+username+'",\n'
                '   "adminpass" => "'+logpasswd+'",\n'
                '   "dbtype" => "mysql",\n'
                '   "dbname" => "'+dbinfo['name']+'",\n'
                '   "dbuser" => "'+dbinfo['user']+'",\n'
                '   "dbpass" => "'+dbinfo['passwd']+'",\n'
                '   "dbhost" => "localhost",\n'
                '   "dbtableprefix" => "",\n'
                '   "directory" => "'+os.path.join(datadir if datadir else path, 'data')+'",\n'
                '   );\n'
                '?>\n'
                )
        os.chown(os.path.join(path, 'config', 'autoconfig.php'), uid, gid)

        # Make sure that the correct PHP settings are enabled
        php.enable_mod('mysql', 'pdo_mysql', 'zip', 'gd',
            'iconv', 'openssl', 'xcache')
        
        # Make sure xcache has the correct settings, otherwise ownCloud breaks
        with open('/etc/php/conf.d/xcache.ini', 'w') as f:
            f.writelines(['extension=xcache.so\n',
                'xcache.size=64M\n',
                'xcache.var_size=64M\n',
                'xcache.admin.enable_auth = Off\n',
                'xcache.admin.user = "admin"\n',
                'xcache.admin.pass = "'+secret_key[8:24]+'"\n'])

    def pre_remove(self, site):
        datadir = ''
        if os.path.exists(os.path.join(site.path, 'config', 'config.php')):
            with open(os.path.join(site.path, 'config', 'config.php'), 'r') as f:
                for line in f.readlines():
                    if 'datadirectory' in line:
                        data = line.split("'")[1::2]
                        datadir = data[1]
        elif os.path.exists(os.path.join(site.path, 'config', 'autoconfig.php')):
            with open(os.path.join(site.path, 'config', 'autoconfig.php'), 'r') as f:
                for line in f.readlines():
                    if 'directory' in line:
                        data = line.split('"')[1::2]
                        datadir = data[1]
        if datadir:
            shutil.rmtree(datadir)
            php.open_basedir('del', datadir)

    def post_remove(self, site):
        pass

    def ssl_enable(self, path, cfile, kfile):
        # First, force SSL in ownCloud's config file
        if os.path.exists(os.path.join(path, 'config', 'config.php')):
            px = os.path.join(path, 'config', 'config.php')
        else:
            px = os.path.join(path, 'config', 'autoconfig.php')
        with open(px, 'r') as f:
            ic = f.readlines()
        oc = []
        found = False
        for l in ic:
            if '"forcessl" =>' in l:
                l = '"forcessl" => true,\n'
                oc.append(l)
                found = True
            else:
                oc.append(l)
        if found == False:
            for x in enumerate(oc):
                if '"dbhost" =>' in x[1]:
                    oc.insert(x[0] + 1, '"forcessl" => true,\n')
        with open(px, 'w') as f:
            f.writelines(oc)

        # Next, update the ca-certificates thing to include our cert
        # (if necessary)
        if not os.path.exists('/usr/share/ca-certificates'):
            try:
                os.makedirs('/usr/share/ca-certificates')
            except OSError, e:
                if e.errno == errno.EEXIST and os.path.isdir('/usr/share/ca-certificates'):
                    pass
                else:
                    raise
        shutil.copy(cfile, '/usr/share/ca-certificates/')
        fname = cfile.rstrip('/').split('/')[-1]
        with open('/etc/ca-certificates.conf', 'r') as f:
            ic = f.readlines()
        oc = []
        for l in ic:
            if l != fname+'\n':
                oc.append(l)
        oc.append(fname+'\n')
        with open('/etc/ca-certificates.conf', 'w') as f:
            f.writelines(oc)
        shell('update-ca-certificates')

    def ssl_disable(self, path):
        if os.path.exists(os.path.join(path, 'config', 'config.php')):
            px = os.path.join(path, 'config', 'config.php')
        else:
            px = os.path.join(path, 'config', 'autoconfig.php')
        with open(px, 'r') as f:
            ic = f.readlines()
        oc = []
        found = False
        for l in ic:
            if '"forcessl" =>' in l:
                l = '"forcessl" => false,\n'
                oc.append(l)
                found = True
            else:
                oc.append(l)
        if found == False:
            for x in enumerate(oc):
                if '"dbhost" =>' in x[1]:
                    oc.insert(x[0] + 1, '"forcessl" => false,\n')
        with open(px, 'w') as f:
            f.writelines(oc)