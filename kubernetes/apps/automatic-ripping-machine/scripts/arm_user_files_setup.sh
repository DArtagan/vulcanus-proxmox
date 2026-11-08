#!/bin/bash
# This script is first to run due to this: https://github.com/phusion/baseimage-docker#running_startup_scripts.
set -euo pipefail

export ARM_HOME="/root"

echo "Adding users to 'render' group"
usermod -a -G render arm
usermod -a -G render root

### Setup Files

touch /root/logs/arm.log
mkdir -m 777 /home/arm/logs
ln -s /root/logs/arm.log /home/arm/logs/arm.log

# setup needed/expected dirs if not found
SUBDIRS="video/completed video/raw video/transcode logs/progress .MakeMKV"
for dir in $SUBDIRS ; do
    thisDir="$ARM_HOME/$dir"
    if [[ ! -d "$thisDir" ]] ; then
        echo "Creating dir: $thisDir"
        mkdir -p "$thisDir"
    fi
done

# Where abcde writes a CD rip before arm-audio-handoff.sh moves it into the
# inbox. Container-local and ephemeral: anything still here after a restart
# is a rip that died before handing over, and re-inserting the disc is the
# recovery.
mkdir -p /home/arm/arm-incoming

ln -s /root/.MakeMKV/ /home/arm/.MakeMKV

# Edit ARM's source. Deliberately not fatal: each edit that cannot apply logs
# and is skipped, and the rest still go in, as a missing fix should not stop
# ARM starting.
python3 /usr/local/bin/arm-source-patches.py || \
    echo "WARNING: arm-source-patches.py failed; see /home/arm/logs/arm.log"

# abcde.conf is expected in /etc by the abcde installation
cp --no-clobber /opt/arm/setup/.abcde.conf /etc/arm/config/abcde.conf
mkdir -p /etc/root/config/
ln -sf /etc/arm/config/abcde.conf /etc/root/config/abcde.conf
