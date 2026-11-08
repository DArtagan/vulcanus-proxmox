#!/bin/bash
# Gatekeeper called by udev on any block device change event. Two things
# must be true before starting ARM:
#
#   1. A disc is present with the tray closed (CDROM_DRIVE_STATUS ==
#      CDS_DISC_OK). Without this the eject event, which the container does
#      receive, triggers a rip attempt against an empty drive.
#   2. udev has published at least one ID_CDROM_MEDIA_* property for the
#      device. Job.parse_udev() derives disctype from those alone, and the
#      drive answers CDS_DISC_OK before its TOC has been read — so ARM
#      started on the first of the events an insert generates fails with
#      "Could not determine disc type" and leaves the drive holding that
#      job, which blocks every later insert until someone abandons it by
#      hand.
#
# Match ID_CDROM_MEDIA_ and not bare ID_CDROM_. The latter (ID_CDROM_BD,
# ID_CDROM_DVD, ...) reports what the drive is capable of reading and is
# present with the tray open.
set -u

DEVNAME="$1"
DEV="/dev/${DEVNAME}"
ARMLOG="/home/arm/logs/arm.log"

# The drive needs a few seconds to read the TOC, longer for a Blu-ray than
# for a CD.
MEDIA_TIMEOUT=60
MEDIA_INTERVAL=2

log() {
    echo "$(date) [ARM] disc-wrapper: $*" >> "${ARMLOG}"
}

# One insert generates several change events, and an ATA link reset during a
# rip generates more. The lock is inherited through the exec below, so it is
# held for the life of the job and only the first event gets one.
exec 9>"/run/lock/arm-disc-${DEVNAME}.lock"
if ! flock -n 9; then
    log "a job already holds ${DEV}, skipping"
    exit 0
fi

STATUS=$(python3 -c "
import fcntl, os
try:
    fd = os.open('${DEV}', os.O_RDONLY | os.O_NONBLOCK)
    s = fcntl.ioctl(fd, 0x5326, 0)  # CDROM_DRIVE_STATUS
    os.close(fd)
    print(s)
except Exception:
    print(0)
" 2>/dev/null)

# CDS_DISC_OK=4: disc present and tray closed. Anything else, skip.
if [ "${STATUS}" != "4" ]; then
    log "no disc in ${DEV} (status=${STATUS}), skipping"
    exit 0
fi

waited=0
until udevadm info "${DEV}" 2>/dev/null | grep -q "ID_CDROM_MEDIA_"; do
    if [ "${waited}" -ge "${MEDIA_TIMEOUT}" ]; then
        log "udev published no ID_CDROM_MEDIA_* for ${DEV} after ${MEDIA_TIMEOUT}s, skipping"
        exit 0
    fi
    sleep "${MEDIA_INTERVAL}"
    waited=$(( waited + MEDIA_INTERVAL ))
done

# udev starts this with no HOME, and makemkvcon finds its registration key
# through $HOME/.MakeMKV. Without it MakeMKV runs unregistered and, once its
# version has expired, refuses every disc with "This application version is
# too old". ARM runs the job as root and writes the key to /root/.MakeMKV.
export HOME=/root

# The job runs on every CPU but the first, which keeps one free for the
# node's other tenants. A CPU limit cannot: HandBrake spends its quota on
# all the CPUs at once early in each period, and everything else on the node
# waits behind it until it is throttled. MakeMKV and HandBrake inherit the
# mask.
exec taskset -c "1-$(( $(getconf _NPROCESSORS_ONLN) - 1 ))" \
    /opt/arm/scripts/docker/docker_arm_wrapper.sh "${DEVNAME}"
