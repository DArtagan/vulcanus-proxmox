# vulcanus-proxmox
Starting with Proxmox &amp; Ansible and building out from there


# Required environment variables
DOMAIN=example.com
PUBLIC_KEY=ssh-ed25519 asdfasdfasdfasdfasdf willam@example.com
PROXMOX_API_USER=proxmox_api_user@pam
PROXMOX_API_PASSWORD=password
WIREGUARD_PEER_PUBLIC_KEY=ASDFSADFSADFASDFASDF


## Principles

* Data files are snapshotted by ZFS and backed up for remote sync by borg.
* VMs and such are backed up by Proxmox Backup Server, with retention controlled in Proxmox.  We keep a minimal number of ZFS snapshots of those locations (just enough to rule out short term error).


## Ad-hoc Kubernetes

Use [k9s](https://k9scli.io/), it's amazing for peering at kubernetes cluster resources.  The logs can be a bit finicky though, so still use `kubectl logs` as necessary.

Useful line for ad-hoc operations (where `beets-import-27836809-c6wsg` is a current pod, `beets-debug` is a replica pod you will make from it, `beets-import` is the container name in that pod to attach to):

```
kubectl debug -n apps -it --copy-to=beets-debug --container=beets-import beets-import-27836809-c6wsg -- sh
```


## Data migration to Kubernetes

Using this example deployment, notice the initContainer and `rancher-key` Volume, which points to an SSH key that can be used to connect to the old server.

```
apiVersion: apps/v1
kind: Deployment
metadata:
  name: niftyapp
  labels:
    app: niftyapp
spec:
  selector:
    matchLabels:
      app: niftyapp
  template:
    metadata:
      labels:
        app: niftyapp
    spec:
      initContainers:
        - name: config-data
          image: debian
          command: ["/bin/sh", "-c"]
          args: ["apt update; apt install -y rsync openssh-client; rsync -vrtplDog --append-verify --chown=1000:1000 rancher@192.168.0.112:/home/rancher/docker-vulcanus/nifty/config/* /data/"]
          volumeMounts:
            - mountPath: /data
              name: config
            - name: rancher-key
              mountPath: /root/.ssh/
              readOnly: true
      containers:
        - name: niftyapp
          image: nifty/app
          ports:
            - containerPort: 32400
          volumeMounts:
            - mountPath: /config
              name: config
      volumes:
        - name: config
          persistentVolumeClaim:
            claimName: nifty-config-pvc
        - name: rancher-key
          secret:
            secretName: rancher-key
            defaultMode: 0400
            items:
              - key: ssh-privatekey
                path: id_rsa
              - key: ssh-publickey
                path: id_rsa.pub
              - key: known_hosts
                path: known_hosts
```

## Hard drive management & disk replacement

Follow the guide at [docs/disk_management.md](docs/disk_management.md)

## Networking notes

* 192.168.0.202: IP address for CoreDNS.  Designed to be a DNS server for the whole internal network (including and beyond kubernetes).
* 192.168.0.203: IP address for the internal kubernetes ingress controller.  CoreDNS (192.168.0.202) will fall through to this for any *.immortalkeep.com domains.



## Increase VM disk sizew

1. SSH into the Proxmox host
2. Run `qm resize 910 virtio1 +512G`. Where `910` is the VM ID, `virtio1` is the disk ID, and `+512G` is the number of Gigabytes to add.
3. Back in this repo, edit `terraform/main.tf` and update the `openebs_disk_size` to match the new VM disk size.
4. `tofu apply`


## Troubleshooting

### talos-worker VM doesn't start, sits on booting HDD screen

In the arguments for the talos-worker VM is a virtual SCSI that expects a cdrom drive to be connected to the host.  No cdrom drive, no launch.


### Plex is failing to start

`kubectl exec` into the Plex container and see if the file `/config/Library/Application\ Support/Plex\ Media\ Server/Preferences.xml` is empty.  If so, delete it and then recreate the pod.  Then launching Plex in a browser might not work, because it hasn't been locally claimed.  To claim it locally do a `kubectl port-forward -n apps plex-blahblah-blah 32400:32400` (with the proper pod name) to forward its local port to your machine, and then visit `http://localhost:32400/web/index.html` in your browser (yes the full URL is important).

### Mumble is failing to start, plex is kinda unreachable

Especially if Mumble's logs say that it can't write to the database, this is likely a sign that that the VM disk is full.  Increase its size.

### Proxmox Backup Manager (PBM) out of disk space

1. SSH into the Proxmox host
2. `qm resize 107 virtio1 +548G`
3. SSH into the Proxmox Backup Manager host `ssh root@192.168.0.107`
4. Confirm you're operating on the right disk: `lsblk`
5. `sgdisk -e /dev/vdb`
6. `sgdisk -d 1 /dev/vdb`
7. `sgdisk -N 1 /dev/vdb`
8. `partprobe /dev/vdb`
9. `resize2fs /dev/vdb1`

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

### Ansible connection fails with `Failed to connect to the host via ssh: ssh_askpass: exec(): No such file or directory
`

Ansible can't handle asking about SSH keys with passphrases, so we need to use an ssh-agent instead.  Made more difficult if running in `fish` shell:
```
eval (ssh-agent -c)
ssh-add ~/.ssh/id_ed25519
ansible-playbook wireguard.yaml
```

## References
* https://www.nathancurry.com/blog/14-ansible-deployment-with-proxmox/
* Replacing a ZFS Proxmox boot disk: http://r00t.dk/post/2022/05/02/proxmox-ve-7-replace-zfs-boot-disk/
* Grow Talos volume: https://www.agos.one/resize-additional-disks-in-siderolabs-talos-linux/
