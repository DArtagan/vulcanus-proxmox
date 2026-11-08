#!/bin/bash
# Replaces upstream's scripts/docker/runit/arm_user_files_setup.sh, the first
# script phusion's my_init runs. ARM runs as root here, with its files under
# /root, and upstream's cannot: it moves the arm user to ARM_UID, and usermod
# refuses uid 0 as taken, which under its `set -e` stops the container.
#
# my_init runs this again when the container restarts within its pod, over
# what the last run left, so every step has to succeed a second time.
set -euo pipefail

# Upstream's wrapper and these scripts log to the arm user's home.
mkdir -p /home/arm/logs
touch /root/logs/arm.log
ln -sfn /root/logs/arm.log /home/arm/logs/arm.log

# A job run as root keeps MakeMKV's key in /root/.MakeMKV, and makemkv.py and
# update_key.sh read it from /home/arm/.MakeMKV.
mkdir -p /root/.MakeMKV
ln -sfn /root/.MakeMKV /home/arm/.MakeMKV

# Where abcde writes a CD rip before arm-audio-handoff.sh moves it into the
# inbox. Container-local and ephemeral: anything still here after a restart
# is a rip that died before handing over, and re-inserting the disc is the
# recovery.
mkdir -p /home/arm/arm-incoming

# Edit ARM's source. Deliberately not fatal: each edit that cannot apply logs
# and is skipped, and the rest still go in, as a missing fix should not stop
# ARM starting.
python3 /usr/local/bin/arm-source-patches.py ||
    echo "WARNING: arm-source-patches.py failed; see /home/arm/logs/arm.log"
