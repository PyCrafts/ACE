#!/usr/bin/env bash
#
# installs any packages required by ACE on an Ubuntu machine
#

source installer/common.sh

if [ "$EUID" != "0" ]
then
	echo "this script must be executed as root"
	exit 1
fi

echo "installing required packages..."
apt-get -y install \
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
    python-pip \
    python3-pip \
	poppler-utils \
    rng-tools \
    memcached \
    mysql-server || fail "package installation failed"

# things that have been removed
# freetds-dev

if ! npm -g ls | grep esprima > /dev/null 2>&1
then 
    npm config set strict-ssl false

    # npm can't seem to use the env proxy settings
    if [ ! -z "$http_proxy" ]
    then
        npm config set proxy "$http_proxy"
    fi
    
    if [ ! -z "$https_proxy" ]
    then
        npm config set https-proxy "$https_proxy"
    fi

    npm -g install esprima || fail "npm package installation failed"
fi

exit 0