#!/usr/bin/env bash

# make sure we're in the installation directory
( cd $(dirname $0) && cd .. ) || { echo "cannot move into installation directory"; exit 1; }

source "installer/common.sh"

# does the ace group exist yet?
if ! grep ^ace: /etc/group > /dev/null 2>&1
then
    echo "creating group ace"
    sudo groupadd ace || fail
fi

# does the ace user exist yet?
if ! id -u ace > /dev/null 2>&1
then
    echo "creating user ace"
    sudo useradd -c 'funtional account for ACE' -g ace -m ace
fi

if [ ! -d /opt/ace ]
then
    # create the main installation directory (hard coded to /opt/ace for now...)
    sudo install -o ace -g ace -d /opt/ace
    # create the directory structure
    sudo find . -type d ! -ipath '*/.git*' -exec install -v -o ace -g ace -d '/opt/ace/{}' \;
    # copy all the files over
    find . -type f ! -ipath '*/.git*' -print0 | sed -z -n -e p -e 's;^\./;/opt/ace/;' -e p | sudo xargs -0 -n 2 install -v -o ace -g ace
    # and then copy the permissions of the files
    find . -type f ! -ipath '*/.git*' -print0 | sed -z -n -e h -e 's;^;--reference=;' -e p -e x -e 's;^\./;/opt/ace/;' -e p | sudo xargs -0 -n 2 chmod
    # create required empty directories
    for d in \
        data \
        logs \
        archive \
        archive/email \
        archive/smtp_stream \
        error_reports \
        malicious \
        scan_failures \
        stats \
        storage \
        var \
        vt_cache \
        work 
    do
        sudo install -v -o ace -g ace -d /opt/ace/$d
    done
fi

echo "installing required packages..."
sudo apt-get -y install \
    nmap \
    libldap2-dev \
    libsasl2-dev \
    libmysqlclient-dev \
    libffi-dev \
    libimage-exiftool-perl \
    p7zip-full \
    p7zip-rar \
    unzip \
    unrar \
    libxml2-dev libxslt1-dev \
    libyaml-dev \
    npm \
    ssdeep \
    python3-pip \
    mysql-server || fail

# things that have been removed
# freetds-dev

#sudo ln -s /usr/bin/nodejs /usr/local/bin/node
if ! npm -g ls | grep esprima > /dev/null 2>&1
then 
    sudo npm config set strict-ssl false
    # sudo npm config set proxy "$http_proxy"
    # sudo npm config set https-proxy "$https_proxy"
    sudo npm -g install esprima
fi

echo "installing required python modules..."
# TODO replace with a requirements file
sudo -E python3 -m pip install --upgrade pip || fail
sudo -E python3 -m pip install --upgrade six || fail

sudo -E python3 -m pip install Flask==0.10.1 || fail
sudo -E python3 -m pip install Flask-Bootstrap==3.3.2.1 || fail
sudo -E python3 -m pip install Flask-Login==0.2.11 || fail
sudo -E python3 -m pip install Flask-Script==2.0.5 || fail
sudo -E python3 -m pip install Flask-WTF==0.11 || fail
#sudo -E python3 -m pip install Jinja2==2.7.3 || fail
sudo -E python3 -m pip install MarkupSafe==0.23 || fail
sudo -E python3 -m pip install PyMySQL==0.6.6 || fail
sudo -E python3 -m pip install SQLAlchemy==1.2.7 || fail
sudo -E python3 -m pip install WTForms==2.0.2 || fail
#sudo -E python3 -m pip install Werkzeug==0.10.4 || fail
#sudo -E python3 -m pip install iptools==0.6.1 || fail
#sudo -E python3 -m pip install itsdangerous==0.24 || fail
sudo -E python3 -m pip install ldap3 || fail
sudo -E python3 -m pip install pyasn1==0.1.8 || fail
sudo -E python3 -m pip install pymongo==2.8 --upgrade || fail
sudo -E python3 -m pip install setuptools_git || fail
#sudo -E python3 -m pip install pymssql || fail
sudo -E python3 -m pip install requests --upgrade || fail
sudo -E python3 -m pip install psutil || fail
sudo -E python3 -m pip install Flask-SQLAlchemy || fail
sudo -E python3 -m pip install pytz || fail
sudo -E python3 -m pip install beautifulsoup4 || fail
sudo -E python3 -m pip install lxml || fail
sudo -E python3 -m pip install python-memcached || fail
sudo -E python3 -m pip install dnspython || fail
sudo -E python3 -m pip install cbapi==1.2.0 || fail
sudo -E python3 -m pip install ply || fail
sudo -E python3 -m pip install businesstime || fail
sudo -E python3 -m pip install html2text || fail
sudo -E python3 -m pip install olefile || fail
sudo -E python3 -m pip install Pandas || fail
sudo -E python3 -m pip install openpyxl || fail
sudo -E python3 -m pip install pysocks || fail
sudo -E python3 -m pip install tld || fail
sudo -E python3 -m pip install python-magic || fail

# install our own custom stuff
sudo -E python3 -m pip install splunklib
sudo -E python3 -m pip install yara_scanner
sudo -E python3 -m pip install vxstreamlib
sudo -E python3 -m pip install urltoolslib

# set up the ACE database
echo "installing database..."
# TODO check to see if this is already done
( sudo mysqladmin create saq-production && \
sudo mysqladmin create ace-workload && \
sudo mysqladmin create brocess && \
sudo mysqladmin create chronos && \
sudo mysqladmin create email-archive && \
sudo mysqladmin create hal9000 && \
sudo mysqladmin create cloudphish && \
sudo mysql --database=saq-production < sql/ace_schema.sql && \
sudo mysql --database=ace-workload < sql/ace_workload_schema.sql && \
sudo mysql --database=brocess < sql/brocess_schema.sql && \
sudo mysql --database=chronos < sql/chronos_schema.sql && \
sudo mysql --database=email-archive < sql/email_archive_schema.sql && \
sudo mysql --database=cloudphish < sql/cloudphish_schema.sql && \
sudo mysql --database=hal9000 < sql/hal9000_schema.sql ) || fail

# set up environment
# TODO do not install globally, just for specific user
#echo | sudo tee -a /etc/bash.bashrc
#echo 'source /opt/ace/load_environment' | sudo tee -a /etc/bash.bashrc

# set up the rest of ace
cd /opt/ace || fail
#ln -s /opt/signatures /opt/ace/etc/yara || fail

export SAQ_HOME=/opt/ace

#echo "downloading ASN data..."
#bin/update_asn_data

#echo "downloading snort rules..."
#bin/update_snort_rules

#echo "creating initial crits cache..."
#bin/update_crits_cache


# TODO these should actually be installed
# link in all the libraries we need
#(cd lib && ln -s /opt/splunklib/splunklib .) || fail
#(cd lib && ln -s /opt/yara_scanner/yara_scanner .) || fail
#(cd lib && ln -s /opt/chronos/chronosapi.py .) || fail
#(cd lib && ln -s /opt/virustotal/virustotal.py .) || fail
#(cd lib && ln -s /opt/wildfirelib/bin/wildfirelib.py .) || fail
#(cd lib && ln -s /opt/vxstreamlib/bin/vxstreamlib.py .) || fail
#(cd lib && ln -s /opt/cbinterface/cbinterface.py .) || fail
#(cd lib && ln -s /opt/cbinterface/CBProcess.py .) || fail
#(cd lib && ln -s /opt/event/lib event) || fail

(cd etc && sudo -u ace cp -a ace_logging.example.ini ace_logging.ini) || fail
(cd etc && sudo -u ace cp -a brotex_logging.example.ini brotex_logging.ini) || fail
(cd etc && sudo -u ace cp -a carbon_black_logging.example.ini carbon_black_logging.ini) || fail
(cd etc && sudo -u ace cp -a email_scanner_logging.example.ini email_scanner_logging.ini) || fail
(cd etc && sudo -u ace cp -a http_scanner_logging.example.ini http_scanner_logging.ini) || fail

# install GUI into apache
# see http://askubuntu.com/questions/569550/assertionerror-using-apache2-and-libapache2-mod-wsgi-py3-on-ubuntu-14-04-python/569551#569551
sudo apt-get -y install apache2 apache2-dev || fail
(   
    sudo -E python3 -m pip install mod_wsgi && \
    sudo mod_wsgi-express install-module > ~/.mod_wsgi-express.output && \
    sed -n -e 1p ~/.mod_wsgi-express.output | sudo tee -a /etc/apache2/mods-available/wsgi_express.load && \
    sed -n -e 2p ~/.mod_wsgi-express.output | sudo tee -a /etc/apache2/mods-available/wsgi_express.conf && \
    rm ~/.mod_wsgi-express.output && \
    sudo a2enmod wsgi_express && \
    sudo a2enmod ssl
    #sudo ln -s /opt/ace/etc/saq_apache.conf /etc/apache2/sites-available/ace.conf && \
    #sudo a2ensite ace && \
    #sudo service apache2 restart
) || fail

# install site configurations for ace
#cp -r --backup=simple --suffix=.backup /opt/site_configs/$customer/ace/* /opt/ace

# create the database user and assign database permissions
#if [ -e "/opt/site_configs/$customer/flags/INSTALL_MYSQL" ]
#then
    #mysql --defaults-file=/opt/sensor_installer/mysql_root_defaults --database=mysql < /opt/site_configs/$customer/ace/sql/saq_users.sql || fail
#fi

# finish ace installation
sudo ln -s /opt/ace/etc/saq_apache.conf /etc/apache2/sites-available/ace.conf && \
sudo a2ensite ace && \
sudo service apache2 restart

#( cd /opt/ace && bin/update_ssdeep )

# update crontabs
#crontab -l 2> /dev/null | cat - crontab | crontab

# install splunk forwarder
#./install_splunk_forwarder.sh $customer

#if [ -x /opt/site_configs/$customer/ace/install ]
#then
    #/opt/site_configs/$customer/ace/install
#fi
