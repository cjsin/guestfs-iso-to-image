# guestfs-iso-to-image
A python script for generating a USB boot image from an ISO file

This script uses 'guestfs' to:

  - create a filesystem image
  - partition it
  - copy isolinux to syslinux and patch boot labels 
  - copy other files from the ISO
  - copy the ISO itself the image
  
  It is currently working with Centos 7 - 1804 Minimal ISO file. Not tested with any other distro or any other centos version.
  
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
 
