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

## References
* https://www.nathancurry.com/blog/14-ansible-deployment-with-proxmox/
