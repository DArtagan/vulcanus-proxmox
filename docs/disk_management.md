## Listing all the hard drive details

```
lsblk -o name,size,model,serial,uuid | grep sd
```


## Disk replacement procedure

1. Take the server out of the rack, open it up.
2. Plug the new disks in, leave them laying atop the server.  Don't unplug or rearrange any disks yet.

3. SSH into the server, start a tmux session.
4. In your tmux session, have three panes (two next to each other on top, one across the bottom)
    a. In the first pane run: `watch -n 1 zpool status`.  This will let you continuously monitor the health of your zfs pool during these operations.  Notice if any hard drives became unseated during removal of the server from the rack.
    b. In the second pane run: `lsblk -o name,size,model,serial,uuid | grep sd`.  This is your reference sheet for hard drive names, sizes, IDs, etc.
    c. In the third pane...
5. Begin testing the new hard drive (here `/dev/sdf`) using:
  ```
  badblocks -wsv -b 4096 /dev/sdf  # Destructive check of the disk for bad sectors, clears all data.
  ```
6. Open additional panes along the bottom as needed and repeat this command for each of the new disks - testing them all in parallel.

7. Once a disk has passed the health check, initialize its partitions:
  ```
  sgdisk /dev/sda -R /dev/sdf  # Copy partition table from old disk to new one (contains 3 paritions)
  sgdisk -G /dev/sdf  # Initialize new guids for partitions on the new disk
  sgdisk -e /dev/sdf  # In case the new disk is bigger than the old one, move the backup headers to the very end of the disk
  sgdisk -d 3 /dev/sdf  # Delete the record of the third parition, which will be the zfs one
  sgdisk -N 3 /dev/sdf  # And re-create it so it now takes up all the remaining disk space
  partprobe /dev/sdf  # Register the new parition & its disk size with the kernel
  proxmox-boot-tool format /dev/sdf2
  proxmox-boot-tool init /dev/sdf2  # Make the new disk bootable
  proxmox-boot-tool refresh  # For good measure, refresh the boot partitions on all disks
  ```
8. Finally, add the new disk into the zfs pool.  Be sure to use the disk-id to make it resilient to sata cables getting re-arranged.  Don't replace more than one disk at a time in the pool (just in case).  Watch the resilver status in the first tmux pane:
  ```
  zpool replace rpool sda-part3 /dev/disk/by-id/sdc-long-disk-id-part3  # Replace the old disk in the pool with this new one
  ```

9. Once each of the new disks has been subbed into the pool, we can re-assemble the server:
  a. Make note of the disk order displayed by `zpool status`
  b. Turn the server off.
  c. Unplug the old hard drives that are being replaced, and the new ones.
  d. Put the new hard drives into the system so that the disk serial IDs on their stickers match the order specified by `zpool status`.  This makes finding a faulted drive in the rack much easier.
  e. Plug everything in and close the system up.  It doesn't particularly matter what motherboard ports the sata cables are plugged in to.  Use locking sata cables to decrease the chances things get knocked loose during transit.
10. Put the server back in the rack and power it on.
11. Run `zpool status` one last time to make sure everything's well.


Note: `zpool set autoexpand=on rpool` needs to have been run once, before the disks were replaced, to tell zfs to autoexpand the vdev when all disks are big enough to support it.  Else you can do it manually with:
  ```
  zpool list  # Show the current size
  zpool set autoexpand=off pool
  zpool online -e pool /dev/disk/by-id/scsi-35000cca27826e008  # Re-bring the disk online in the pool with expansion enabled `-e`
  # Repeat the above command for each disk in the vdev
  zpool set autoexpand=on pool
  zpool list  # The size should be bigger now
  ```

## Hard drive testing and rebuild

Testing a fresh or potentially bad drive
```
badblocks -nsv -b 4096 /dev/sdf  # Non-destructive check of the disk for bad sectors, blocksize=4096.  Substitute `w` for `n` for a destructive test.
```
Badblocks does effectively as good a check as a smartctl long test.


## Hard drive performance test
```
fio --name=random-write --ioengine=posixaio --rw=randwrite --bs=1m --size=16g --numjobs=1 --iodepth=1 --runtime=60 --ti
me_based --end_fsync=1
```
