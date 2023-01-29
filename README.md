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
          args: ["apt update; apt install -y rsync openssh-client; rsync -vrtplD --append-verify --chown=1000:1000 rancher@192.168.0.112:/home/rancher/docker-vulcanus/nifty/config/* /data/"]
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


## References
* https://www.nathancurry.com/blog/14-ansible-deployment-with-proxmox/
