# guestfs-iso-to-image
A python script for generating a USB boot image from an ISO file

This script uses 'guestfs' to:

  - create a filesystem image
  - partition it
  - copy isolinux to syslinux and patch boot labels 
  - copy other files from the ISO
  - copy the ISO itself the image
  
  It is currently working with Centos 7 - 1804 Minimal ISO file. Not tested with any other distro or any other centos version.
  
  Currently under development and likely to have bugs - as it was only written today and I stopped when I got the image successfully booting in a VM.
 
 There is a --fsytpe flag to change the filesystem flag from vfat but I haven't tested it at all.
 
 I haven't yet added a flag to change the image format, it's currently fixed at 'raw'.
 
  
  # Requirements
  
  You will need:
     - libguestfs
     - Python2-guestfs (python bindings)
     - an ISO file
    
  # Example usage:
  
  Build with default mode (copying all files to the disk image):
  
  ```
  build-boot-image.py -i centos.iso -o image.raw \
      --create  --defaults  --verbose
  ```

  Minimal mode - just do the partitioning, and syslinux setup:

  ```
  build-boot-image.py -i centos.iso -o image.raw \
      --create --force --minimal  --verbose
  ```


  Update existing image - copy Packages and repodata from iso: 
  
  ```
  build-boot-image.py -i centos.iso -o image.raw \
      --update  --copy Packages --copy Repodata  
  ```

  Rebuild with Centos7 defaults, overwriting the previous file:
  
  ```
  build-boot-image.py -i centos.iso -o image.raw \
      --force --create  --centos7 
  ```
 
  Edit a sysconfig file of an existing image to delete redhat 'quiet' option on kernel boot lines (match LABEL, replace 'quiet' with ''):
  
  ```
  ./build-boot-image.py -i centos.iso -o image.raw \
      --update -edit /usb/sysconfig/sysconfig.cfg --sed LABEL= quiet '' 
  ```
 
  # Example session (short, not verbose)
  
  This example shows copying a kickstart from the host. Paths specified with '--copy' are assumed to be on the ISO unless they are prefixed with './' or '/':
  
  ```
  $ ./build-boot-image.py -i ~/centos7-isos/CentOS-7-x86_64-Minimal-1804.iso -o ./boot-image.raw  --force --create --defaults --copy ./kickstart.cfg  --edit /usb/syslinux/syslinux.cfg --sed LABEL= ' quiet' ''
Copy isolinux dir as syslinux
Install syslinux files
Update volume label (auto) (updating sysconfig boot lines too)
Autolabel - found label CentOS 7 x86_64
Warning - filesystem label is being truncated to 11 characters for vfat
Updated file successfully
Filesystem label is CentOS 7 x8
Copy all the iso files (excepts isolinux dir) to the image
Copy ('./kickstart.cfg',)
Patch file /usb/syslinux/syslinux.cfg with edit - lines matching 'LABEL=' - change ' quiet' -> ''
Updated file successfully
```


# NOTE:

 the '--copy' can be used multiple times and can be used with the create or update mode, so you can copy extra files along with the initial generation (no second step required).
 
# Bugfixes etc:

Feel free to send updates or bugfixes.

# Longer session (verbose mode enabled):

```
$ ./build-boot-image.py -i ~/centos7-isos/CentOS-7-x86_64-Minimal-1804.iso -o ./boot-image.raw  --force --create --defaults --copy ./kickstart.cfg  --edit /usb/syslinux/syslinux.cfg --sed LABEL= ' quiet' '' --verbose
BUILD
    Prepare build
        Startup guestfs
        OK
        Access images
            Creating new repartitioned image with size 12288 MB
            OK
        OK
        Partitioning
            Create partitions on /dev/sdb
                Partitions are /dev/sdb1 /dev/sdc1 /dev/sdc2
            OK
            Make vfat filesystem on /dev/sdb1
            OK
            Mark /dev/sdb1 bootable
            OK
        OK
        Mount
            Create mountpoint /usb
            OK
            Mount /usb
            OK
            Create mountpoint /iso
            OK
            Mount /iso
            OK
        OK
    OK
    Mount
    OK
    Copy ISO boot contents
        Copy isolinux as syslinux
        OK
        setup syslinux.cfg
        OK
        Load MBR
        OK
        Write MBR
        OK
        Upload syslinux menu files.
            Upload /usr/share/syslinux/vesamenu.c32 to /usb/syslinux/vesamenu.c32
            OK
            Upload /usr/share/syslinux/libcom32.c32 to /usb/syslinux/libcom32.c32
            OK
            Upload /usr/share/syslinux/libutil.c32 to /usb/syslinux/libutil.c32
            OK
            Upload /usr/share/syslinux/menu.c32 to /usb/syslinux/menu.c32
            OK
        OK
        Umount /usb
        OK
        Run syslinux
        OK
        Mount /usb
        OK
        Update label
Autolabel - found label CentOS 7 x86_64
Warning - filesystem label is being truncated to 11 characters for vfat
            Replacing LABEL=CentOS\x207\x20x86_64 with LABEL=CentOS\x207\x20x8 in lines that match LABEL= in /usb/syslinux/syslinux.cfg
                Updated file successfully
            OK
Filesystem label is CentOS 7 x8
        OK
        Copy * to /usb/ (added /usb)
        OK
        Copying files from iso to  - ('./kickstart.cfg',)
            Upload ./kickstart.cfg to /usb/kickstart.cfg
            OK
        OK
        Edit path /usb/syslinux/syslinux.cfg(edit = LABEL=  quiet )
            Replacing  quiet with  in lines that match LABEL= in /usb/syslinux/syslinux.cfg
                Updated file successfully
            OK
        OK
    OK
    Cleanup
        Umount /usb
        OK
        Umount /iso
        OK
    OK

```
