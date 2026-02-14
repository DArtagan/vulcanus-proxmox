# Talos

## Maintenance

### Upgrade Talos

Rules:
* Only upgrade one machine at a time (first the controlplane at 192.168.0.190, then the worker at 192.168.0.195)
* First upgrade to the highest patch version (1.10.2 -> 1.10.9) in that minor release.  Then upgrade to the highest patch version in the next minor release (1.10.9 -> 1.11.7).  Repeat until you're at the most recent release (1.11.7 -> 1.12.4).

1. Use https://factory.talos.dev to get your image.  If you have multiple upgrades to make, after getting your first image-name, you can manipulate the URL to get subsequent versions without going through the whole wizard again. Relevant options:
  * Bare-metal Machine
  * amd64
  * Extensions:
    * siderolabs/iscsi-tools
    * siderolabs/qemu-guest-agent
    * siderolabs/tailscale
2. Upgrade the machine.  `--stage` seems to help it go smoothly.
```
talosctl --nodes 192.168.0.190 upgrade --stage --image factory.talos.dev/metal-installer/58e4656b31857557c8bad0585e1b2ee53f7446f4218f3fae486aa26d4f6470d8:v1.12.4
```
3. Check that the machine is running the version specified (one way is going into the VNC console and looking at the version displayed in the dashboard's first line).


### Grow Talos volume
1. Create debug namespace: `kubectl create ns debug`
2. Allow pod in created namespace to mount the host: `kubectl label ns debug pod-security.kubernetes.io/enforce=privileged`
3. Create the debug pod: `kubectl debug node/piraeus-worker -it --image ubuntu --profile=sysadmin -n debug`
4. Now inside the debug pod: `apt-get update && apt-get install xfsprogs parted`
5. `parted`
6. Select the correct device: `select /dev/sdb`
7. # parted should warn that not all the space is used, type "Fix" and enter: `Fix`
8. Resize the partition: `resizepart 1 100%`
9. Exit parted: `quit`
10. `xfs_growfs -d /host/var/openebs`
11. You're all in the pod: `exit`
12. `kubectl delete ns/debug`

The grow command should look like:
```
root@piraeus-worker-0:/# xfs_growfs -d /host/var/openebs
meta-data=/dev/vdb1              isize=512    agcount=9, agsize=16777088 blks
         =                       sectsz=512   attr=2, projid32bit=1
         =                       crc=1        finobt=1, sparse=1, rmapbt=0
         =                       reflink=1    bigtime=1 inobtcount=0 nrext64=0
data     =                       bsize=4096   blocks=134217216, imaxpct=25
         =                       sunit=0      swidth=0 blks
naming   =version 2              bsize=4096   ascii-ci=0, ftype=1
log      =internal log           bsize=4096   blocks=32767, version=2
         =                       sectsz=512   sunit=0 blks, lazy-count=1
realtime =none                   extsz=4096   blocks=0, rtextents=0
data blocks changed from 134217216 to 268435195
```


## Troubleshooting

### talos-worker VM doesn't start, sits on booting HDD screen

In the arguments for the talos-worker VM is a virtual SCSI that expects a cdrom drive to be connected to the host.  No cdrom drive, no launch.


## References
* Grow Talos volume: https://www.agos.one/resize-additional-disks-in-siderolabs-talos-linux/
