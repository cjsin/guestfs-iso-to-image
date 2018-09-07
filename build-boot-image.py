#!/usr/bin/env python3

# This script was developed using the following one as a great example to get started:
#    https://access.redhat.com/documentation/en-us/red_hat_openstack_platform/10/html/director_installation_and_usage/appe-whole_disk_images


from collections import OrderedDict
import guestfs
import os
import sys
import re
import argparse
import traceback

builder = None 

def die(msg):
    global builder
    print(msg, file=sys.stderr)
    if builder:
        builder.cleanup()
    sys.exit(1)

class Syslinux:
    DEFAULT_PATH="/usr/share/syslinux"
    DEFAULT_MENU_FILES=['vesamenu.c32','libcom32.c32', 'libutil.c32','menu.c32']   
    DEFAULT_MBR_FILE='mbr.bin'

    def __init__(self,rootpath=DEFAULT_PATH,menufiles=DEFAULT_MENU_FILES,mbrfile=DEFAULT_MBR_FILE):
        self.rootpath = rootpath
        self.menufiles = menufiles
        self.mbrfile = mbrfile 

    def file_path(self,name):
        path = os.path.join(self.rootpath,name)
        if os.path.exists(path):
            return path
        else:
            print("Warning: File "+path+" was not found!")
            return None

    def mbr_file(self):
        return self.file_path(self.mbrfile)

    def file_paths(self,names=None):
        if names is None:
            names=self.menufiles
        
        ret=[]
        
        for name in names:
            path = self.file_path(name)
            if path:
                ret.append(path)
        return ret


class Action:
    def __init__(self,*items,**kwargs):
        self.items=items
        self.args=kwargs
    def run(self,builder):
        return False

class CopyAction(Action):
    def run(self,builder):
        builder.copy_files_generic(self.items)
        return True
    def __str__(self):
        return "Copy {}".format(self.items)

class CopyGlobAction(Action):
    def run(self,builder):
        builder.copy_files_generic(self.items[0])
        return True
    def __str__(self):
        return "Copy paths matching {} to the image".format(self.items[0])

class CopyAllIsoFilesAction(Action):
    def run(self,builder):
        builder.copy_all_from_iso_to_path(ignore_isolinux=True)
        return True
    def __str__(self):
        return "Copy all the iso files (excepts isolinux dir) to the image"

class CopyIsoFileAction(Action):
    def run(self,builder):
        builder.copy_iso_file()
        return True
    def __str__(self):
        return "Copy ISO File"

class CopyIsolinuxAsSyslinuxAction(Action):
    def run(self,builder):
        builder.copy_isolinux_as_syslinux()
        return True
    def __str__(self):
        return "Copy isolinux dir as syslinux"
        
class SyslinuxAction(Action):
    def run(self,builder):
        mbr_file=self.args['mbr']
        menufiles=self.args['menufiles']
        src=self.args['src'] 

        builder.install_syslinux(src,mbr_file,menufiles)
        return True
    def __str__(self):
        return "Install syslinux files"

class PatchFileAction(Action):
    def __init__(self,path,edit1,edit2,edit3=None):
        if edit3 is None:
            super().__init__(path,'.*',edit1,edit2)
        else:
            super().__init__(path,edit1,edit2,edit3)
    def run(self,builder):
        builder.edit_path(*self.items)
        return True
    def __str__(self):
        return "Patch file {} with edit - lines matching '{}' - change '{}' -> '{}'".format(*self.items)

class UpdateLabelAction(Action):
    def __init__(self,label,patch_sysconfig_labels):
        super().__init__(label=label if label else 'auto',patch_sysconfig_labels=patch_sysconfig_labels)
    def run(self,builder):
        builder.update_label(self.args['label'], patch_sysconfig_labels=self.args['patch_sysconfig_labels'])
        return True
    def __str__(self):
        return "Update volume label ({}){}".format(self.args['label'], ' (updating sysconfig boot lines too)' if self.args['patch_sysconfig_labels'] else '')

class InspectAction(Action):
    def run(self,builder):
        builder.inspect_paths(self.items)
        return True
    def __str__(self):
        return "Inspect paths {}".format(self.items)

class CreateAction(Action):
    def run(self,builder):
        builder.prepare_for_build(self.args.size)
        return True
    def __str__(self):
        return "Create image file"

class BeginUpdateAction(Action):
    def run(self,builder):
        builder.prepare_for_update()
        return True
    def __str__(self):
        return "Update existing image file"

class VerboseBase:
    """ Base class to assist in seeing what's going on """
    def __init__(self,
                 verbose=0,
                 quiet=False,
                 indent_base="    "
                 ):
        self.verbose=verbose
        self.quiet=quiet
        self.indent=""
        self.indent_base=indent_base

    def msg(self,message):
        print(self.indent+message,file=sys.stderr)

    def begin(self,msg):
        if self.verbose:
            print(self.indent + msg, file=sys.stderr)
            self.indent += self.indent_base

    def end(self,ok=True):
        if self.verbose:
            unindent = len(self.indent_base)
            self.indent = self.indent[:-unindent]
            if ok:
                self.msg("OK")
            else:
                self.msg("Failed")
                raise ValueError("Something failed")

class ImageAccess(VerboseBase):
    """ Automate creation or access of image file and ISO to
    prepare for further operations """

    DEFAULT_SIZE=12*1024

    def __init__(self,
                 isofile=None,
                 usbfile=None,
                 fstype=None,
                 force=False,
                 verbose=0,
                 quiet=False,
                 indent_base="    "
                 ):
        # print("ImageAccess init with isofile "+isofile) # + str(kwargs))
        super().__init__(verbose=verbose,quiet=quiet,indent_base=indent_base)

        self.isofile=isofile
        self.usbfile=usbfile
        self.fstype=fstype or 'vfat'
        self.force=force

        self.iso_index=None
        self.usb_index=None
        self.usbpart=None
        self.g=None
        self.usb=None
        self.iso=None
        self.mounts={}

    def startup(self):
        self.begin("Startup guestfs")
        self.g = guestfs.GuestFS(python_return_dict=True)
        self.g.launch()
        self.end()
        
    def cleanup(self):
        self.begin("Cleanup")
        if self.g:
            self.umount()
            self.g.shutdown()
            self.g.close() 
            self.g = None
        self.end()

    def scan_partitioning(self):
        self.begin("Determine partitioning")

        if self.usb is None:
            self.msg("No usb/disk image device available for partitioning")
        elif self.usbpart is None:
            #devices = g.list_devices()
            #usbdev = devices[usb_index]
            partitions = self.g.list_partitions()

            # As long as the usb device is always added first,
            # this will be the first partition - what to do
            # though, in inspect mode, with only an ISO?
            for part in partitions:
                if part.startswith(self.usb):
                    self.msg("Found USB/image partition "+part)
                    self.usbpart = part

        self.end()

    def define_images(self,create=False,size=None):
        g = self.g

        self.begin("Access images")

        if size is None or not size:
            size = ImageAccess.DEFAULT_SIZE

        if self.usb or self.iso:
            self.msg("Already defined.")
        else:
            device_index=0
            # import old and new images
            if create:
                self.begin("Creating new repartitioned image with size {} MB".format(size))
                g.disk_create(self.usbfile, "raw", size * 1024 * 1024) 
                self.end()

            if os.path.exists(self.usbfile):
                self.usb_index=device_index
                g.add_drive_opts(self.usbfile, format="raw", readonly=0, label="usb")
                device_index+=1
                devices = g.list_devices()
                assert(len(devices) == device_index)
                self.usb = devices[self.usb_index]
            else:
                self.msg("No USB image available")

            if os.path.exists(self.isofile):
                self.iso_index=device_index
                g.add_drive_opts(self.isofile, format="raw", readonly=1,label="iso")
                device_index+=1
                devices = g.list_devices()
                assert(len(devices) == device_index)
                self.iso = devices[self.iso_index]
            else:
                self.msg( "No ISO file available")

        self.end()
        return (self.usb,self.iso)

    def create_partitioning(self):
        self.begin("Partitioning")

        usb = self.usb
        g = self.g

        # create the partitions for new image
        self.begin("Create partitions on " + usb)
        g.part_init(usb, "mbr")
        g.part_add(usb, "primary", 2048, -1)

        partitions = g.list_partitions()
        #assert(len(partitions) == 1)
        if self.verbose:
            self.msg("Partitions are "+" ".join(partitions))
        # return 
        self.end()
        
        usbpart = partitions[0]

        self.begin("Make " + self.fstype+" filesystem on " + usbpart)
        g.mkfs(self.fstype, usbpart)
        self.end()
        
        self.begin("Mark "+usbpart+" bootable")
        g.part_set_bootable(usb, 1, True)
        self.end()

        self.usbpart = usbpart 

        self.end()

    def mount(self):
        self.begin("Mount")
        self.mount_usb()
        self.mount_iso()
        self.end()

    def mount_iso(self):
        self.mount_dev(self.iso,'iso')

    def mount_usb(self):
        self.mount_dev(self.usbpart,'usb')

    def mount_dev(self,dev,name):
        if not self.g:
            return

        path='/'+name
        if name not in self.mounts:
            self.begin("Create mountpoint "+path)
            self.g.mkmountpoint(path)
            self.end()
            self.mounts[name]=None

        if not self.mounts[name]:
            self.begin("Mount "+path)
            self.g.mount(dev,path)
            self.end()
            self.mounts[name]=path

    def umount_dev(self,name):
        if not self.g:
            return
        path='/'+name
        if name in self.mounts and self.mounts[name]:
            self.begin("Umount "+path)
            self.g.umount(path)
            self.mounts[name]=None
            self.end()

    def umount_iso(self):
        self.umount_dev('iso')

    def umount_usb(self):
        self.umount_dev('usb')
    
    def umount(self):
        for name in self.mounts:
            self.umount_dev(name)

    def display_lines(self, lines):
        for x in lines:
            print(self.indent + x)

    def ls(self,path,display=False):
        results = self.g.ls(path)
        if display:
            self.begin("Listing of "+path+":")
            self.display_lines(results)
            self.end()
        return results   

    def read_file(self,path):
        if self.g.exists(path):
            return self.g.cat(path)
        else:
            return None

    def display_path(self,path,display_contents=False):
        if not self.g.exists(path):
            self.msg("Path "+path+" does not exist")
        else:
            if self.g.is_dir(path):
                self.ls(path,display=display_contents)
            elif self.g.is_file(path):
                if display_contents:
                    content=self.g.cat(path)
                    self.begin("Contents of "+path+":")
                    lines=content.split('\n')
                    self.display_lines(lines)
                    self.end()
                else:
                    self.msg("File "+path+" exists")
                    parent,base = os.path.split(path)

    def delete_old_image(self):

        self.umount_usb()

        try:
            os.unlink(self.usbfile)
        except:
            pass



class ImageBuilderBase(ImageAccess):
    """ Provide some higher level operations """

    def __init__(self,label=None,syslinux=None,
                 **kwargs):

        super().__init__(**kwargs)

        self.label=label
        if syslinux is None:
            syslinux=Syslinux()
        self.syslinux=syslinux

    def read_syslinux_cfg(self):
        return self.read_file('/usb/syslinux/syslinux.cfg')

    def read_isolinux_cfg(self):
        return self.read_file('/usb/isolinux/isolinux.cfg')

    def read_xxxlinux_cfg(self):
        return self.read_syslinux_cfg() or self.read_isolinux_cfg()
        
    def determine_label(self):
        cfgfile=self.read_xxxlinux_cfg()
        if not cfgfile:
            print("No syslinux config file - cannot search for labels")
            return

        lines=cfgfile.splitlines()
        labels=[]

        for line in lines: 
            if not line:
                continue
            parts = line.split(' ')
            for p in parts:
                if 'LABEL=' in p:
                    (start,end)=p.split('LABEL=')
                    end = end.replace('\\x20',' ')
                    if end and end not in labels:
                        labels.append(end)
        if len(labels) > 1:
            print("Autolabel - Multiple potential labels found - please try specifying one of these manually")
            self.display_lines(labels)
        elif not labels:
            print("Autolabel - No labels were found in the syslinux conf")
        else:
            print("Autolabel - found label " + labels[0])
            self.label=labels[0]

    def patch_file(self,filename,line_matcher,oldtext,newtext):
        self.begin("Replacing " + oldtext + " with " + newtext + " in lines that match " + line_matcher +" in "+filename)
        
        if not self.g.exists(filename):
            self.msg("File not found", file=sys.stderr)
        else:
            cfgdata = self.read_file(filename)
            lines = cfgdata.splitlines()
            out=""
            replacements=0
            matched=0
            for line in lines:
                m = re.search(line_matcher,line)
                if m:
                    matched += 1
                    changed = line.replace(oldtext,newtext)
                    if changed != line:
                        replacements+=1
                    out += changed
                else:
                    out += line

                out += "\n"
            if not matched:
                print("No lines matched "+line_matcher)
            elif (not replacements) or out == cfgdata:
                self.msg("The lines were not found (no replacements made).")
            else:
                self.g.write(filename,out)
                self.msg("Updated file successfully")

        self.end()

    def patch_label(self,oldlabel,newlabel):
        syslinux_cfg='/usb/syslinux/syslinux.cfg'
        searchtext = oldlabel.replace(' ','\\x20')
        newtext = newlabel.replace(' ','\\x20')
        self.patch_file(syslinux_cfg,'LABEL=','LABEL='+searchtext,'LABEL='+newtext)

    def update_label(self,label,patch_sysconfig_labels=False):
        self.begin("Update label")

        if not label or label == 'auto':
            if (not self.label) or (self.label == 'auto'):
                self.determine_label()
                label=self.label
        
        if label:
            fslabel=label
            if self.fstype == 'vfat' and len(label) > 11:
                print("Warning - filesystem label is being truncated to 11 characters for vfat")
                fslabel=fslabel[0:11]

            self.g.set_label(self.usbpart, fslabel)

            if fslabel != self.label:
                if patch_sysconfig_labels:
                    self.patch_label(label,fslabel)
                else:
                    print("Warning: Actual filesystem label is different than desired but syslinux.cfg patching is not enabled.")
            self.label = fslabel

        print("Filesystem label is " + self.g.vfs_label(self.usbpart))

        self.end()


    def copy_isolinux_as_syslinux(self):
        self.begin("Copy ISO boot contents")
        g = self.g
        if 'isolinux' in self.ls('/iso/'):
            self.begin("Copy isolinux as syslinux")
            g.cp_a('/iso/isolinux','/usb/syslinux')
            self.end()
        self.begin("setup syslinux.cfg")
        g.cp_a('/usb/syslinux/isolinux.cfg','/usb/syslinux/syslinux.cfg')
        self.end()

    def copy_iso_file(self):
        self.begin("Copy ISO")
        self.upload_file(self.isofile)
        self.end()

    def upload_file(self,src,destfolder=""):
        destdir=os.path.join("/usb",destfolder) 
        destpath=os.path.join(destdir,os.path.basename(src))
        self.begin("Upload "+src+" to "+destpath)
        self.g.upload(src, destpath)
        self.end()

    def install_syslinux(self,syslinux_path=None, mbr_file=None, menufiles=None):
        if not self.usb:
            raise ValueError("No image available")

        if not self.g.feature_available(['syslinux']):
            die("no syslinux support")

        if not mbr_file and not menufiles:
            self.msg("Warning - No mbr file or syslinux files were specified for installation")

        if not syslinux_path:
            syslinux_path = self.syslinux.DEFAULT_PATH

        if mbr_file:
            self.begin("Load MBR")
            mbr_data=None
            if not os.path.exists(mbr_file) and mbr_file[0] != '/':
                mbr_file=self.syslinux.file_path(mbr_file)
            with open(mbr_file,"rb") as f:
                mbr_data = f.read()
            self.end()

            self.begin("Write MBR")
            self.g.pwrite_device(self.usb, mbr_data, 0)
            self.end()

        if menufiles:
            self.begin("Upload syslinux menu files.")

            for fpath in self.syslinux.file_paths():
                self.upload_file(fpath,'syslinux')
            self.end()

        # syslinux mode is meant to run with the volume unmounted.
        self.umount_usb()
        self.begin("Run syslinux")
        self.g.syslinux(self.usbpart,'/syslinux')
        self.end()

        # Re-mount the USB
        self.mount_usb()

    def require(self,*what):
        if not self.g:
            raise ValueError("Not ready for update")
        for item in what:
            if item == 'usb' and not self.usb:
                raise ValueError("No image destination available")
            elif item == 'iso' and not self.iso:
                raise ValueError("No iso file source available")
            elif item == '/iso' and not self.mounts['iso']:
                if self.iso:
                    self.mount_iso()
                else:
                    raise ValueError("No iso available")
            elif item == '/usb' and not self.mounts['usb']:
                if self.usb:
                    self.mount_usb()
                else:
                    raise ValueError("No usb/ image available")
                raise ValueError("No iso file source available")

    def copy_path_from_iso(self,item):
        self.copy_path_from_iso_to_path(item,item)

    def path_glob(self,path,pattern):
        prefix=path+"/"
        glob_result= self.g.glob_expand_opts(prefix+pattern,directoryslash=True)
        ret=[]
        for item in glob_result:
            if item.startswith(prefix):
                ret.append(item[len(prefix):])
            else:
                msg("Weird result from glob is ignored:{}".format(item))
        return ret

    def copy_all_from_iso_to_path(self,dstpath="",ignore_isolinux=False):
        self.copy_glob_from_iso_to_path('*',dstpath,ignore_isolinux=ignore_isolinux)

    def copy_glob_from_iso_to_path(self,glob,dstpath="",ignore_isolinux=False):
        dstpath="/usb/"+dstpath
        self.begin("Copy "+glob+" to " + dstpath+" (added /usb)")
        names=self.path_glob("/iso",glob)
        for item in names:
            if ignore_isolinux and item == 'isolinux':
                msg("Subdirectory isolinux is handled specially and ignored when copying '{}' files".format(glob))
            else:
                self.g.cp_a('/iso/'+item,dstpath)
        self.end()

    def copy_path_from_iso_to_path(self,item,dstpath):
        dstpath="/usb/"+dstpath
        self.begin("Copy "+item+" to " + dstpath+" (added /usb)")
        self.require('/iso','/usb')
        if item in self.g.ls('/iso'):
            self.g.cp_a('/iso/'+item,dstpath)
        else:
            self.msg("Not found on iso:"+item)
        self.end()

    def prepare_for_inspection(self):
        """ 
        Inspection allows for not having the USB image or ISO available.
        Additionally, inspection mode can be re-entered after update or create mode 
        """
        self.begin("Prepare for inspection")

        if not self.g:
            self.startup()
        
        self.define_images(create=False)
        self.scan_partitioning()
        self.mount()

        self.end()

    def prepare_for_update(self, require_iso=False):
        """ Update mode requires at least a USB file available """
        self.begin("Prepare for Update")
        if not os.path.exists(self.usbfile):
            raise ValueError("Output file does not yet exist!")

        if require_iso and not os.path.exists(self.isofile):
            raise ValueError("Iso file does not exist!")

        self.startup()
        self.define_images(create=False)
        self.scan_partitioning()
        self.mount()

        self.end()

    def prepare_for_build(self,size=None):
        self.begin("Prepare build")

        if not ( self.isofile and os.path.exists(self.isofile)):
            raise ValueError("Iso file does not exist!")

        if self.usbfile and os.path.exists(self.usbfile):
            if self.force:
                self.delete_old_image()
            else:
                raise ValueError("Output file exists - set force flag to overwrite")
        
        self.startup()
        self.define_images(create=True,size=size)
        self.create_partitioning()
        self.mount()

        self.end()

    def copy_files_generic(self,items, dstdir=None):
        """ 
        Generic copy which can source files from the iso or the host filesystem 
        """

        if dstdir is None:
            dstdir=""

        self.begin("Copying files from iso to {} - {}".format(dstdir,items))

        for item in items:
            if item == ":isolinux-as-syslinux":
                self.copy_isolinux_as_syslinux(dstdir)
            elif item == ":isofile":
                self.copy_iso_file(dstdir)
            elif item.startswith('/') or item.startswith('./'):
                self.upload_file(item,dstdir)
            else:
                # assume it is contents on the ISO
                self.copy_path_from_iso_to_path(item,dstdir)
        self.end()

    def inspect_paths(self,paths,display_contents=True):
        self.begin("Inspect paths "+" ".join(paths))
        for path in paths:
            self.display_path(path,display_contents=display_contents)
        self.end()

class ImageBuilder(ImageBuilderBase):
    
    def __init__(self,actions=None, **kwargs):

        if actions is None:
            actions=[]

        super().__init__(**kwargs)
        
        self.actions=actions 

    def clear_actions(self):
        self.actions=[]

    def add_action(self, action):
        self.actions.append(action)
    
    def edit_path(self,path, *edit):
        self.begin("Edit path "+path + "(edit = " + " ".join(edit)+")")
        if edit and len(edit) >= 2:
            if len(edit) == 3:
                (pattern,find,replacement)=edit
            elif len(edit) == 2:
                pattern='.*'
                (find,replacement)=edit
            self.patch_file(path,pattern,find,replacement)
        else:
            print("Not enough parameters for edit")

        self.end()

    def perform_actions(self,actions):
        for action in actions:
            if action:
                if not self.verbose and not self.quiet:
                    print(str(action))
                if not action.run(self):
                    return False
        return True 

    def perform_updates(self,actions):
        """ 
        Perform preconfigured actions in update or build mode, if actions is None or unspecified.
        If actions is passed and is not None, those actions will be performed instead.
        To perform no actions at all, pass an empty array.
         """
        self.mount()

        if actions is None:
            actions = self.actions

        result = self.perform_actions(actions)

        return result

    def inspect_mode(self):
        """ Inspect mode will prepare for inspection or update but will not perform any preconfigured updates """
        self.begin("INSPECT")
        self.prepare_for_inspection()
        self.end()
        return True

    def update_mode(self,actions=None):
        """ Update mode will prepare for update and then perform preconfigured updates """
        self.begin("UPDATE")

        self.prepare_for_update()

        result=self.perform_updates(actions)

        self.end(result)

        return result


    def build_mode(self,size=None,actions=None):
        self.begin("BUILD")

        self.prepare_for_build(size)

        result=self.perform_updates(actions)

        self.end(result)

        return result

class DoNothingImageBuilder(ImageBuilder):
    def __init__(self, **kwargs):
        super().__init__([],**kwargs)

class IsoBasedImageBuilder(ImageBuilder):
    """ 
    Some distros apparently build with the iso in the root of the stick, and the images and syslinux and that's it.
    I haven't seen that working, it would likely need customisation of the boot parameters in the syslinux file.
    Nevertheless, here is a basis for that mode.
    """
    def __init__(self, **kwargs):
        actions=[
            CopyIsolinuxAsSyslinuxAction(),
            CopyAction('images'),
            SyslinuxAction( 
                src=Syslinux.DEFAULT_PATH,
                mbr=Syslinux.DEFAULT_MBR_FILE, 
                menufiles=Syslinux.DEFAULT_MENU_FILES
            ),
            CopyIsoFileAction()
        ]
        super().__init__(actions,**kwargs)

class CopyFilesImageBuilder(ImageBuilder):
    """
    The only method I have seen working yet, is to copy the iso files (works with centos 7-1804).
    There is a Centos7 builder below, however this is a more generic one that
    performs a similar action - using a glob instead to copy all the files,
    whereas the centos builder is copying explicitly files known to be on the centos 7 minimal ISO.
    """
    def __init__(self,**kwargs):
        actions=[
            CopyIsolinuxAsSyslinuxAction(),
            SyslinuxAction( 
                src="/usr/share/syslinux",
                mbr='mbr.bin',
                menufiles=[ 'vesamenu.c32','libcom32.c32', 'libutil.c32','menu.c32' ]
            ),
            UpdateLabelAction('auto',patch_sysconfig_labels='True'),
            CopyAllIsoFilesAction()
        ]
        super().__init__(actions,**kwargs)

class Centos7ImageBuilder(ImageBuilder):
    def __init__(self,**kwargs):
        actions=[
            CopyIsolinuxAsSyslinuxAction(),
            CopyAction('images'),
            SyslinuxAction( 
                src="/usr/share/syslinux",
                mbr='mbr.bin',
                menufiles=[ 'vesamenu.c32','libcom32.c32', 'libutil.c32','menu.c32' ]
            ),
            UpdateLabelAction('auto',patch_sysconfig_labels='True'),
            CopyAction(
                'images',
                'repodata',
                'Packages'
                '.discinfo',
                '.treeinfo',
                'CentOS_BuildTag',
                'RPM-GPG-KEY-CentOS-7',
                'RPM-GPG-KEY-CentOS-Testing-7'
            )
        ]
        super().__init__(actions,**kwargs)

def run(args):
    global builder

    try:
        params=dict(OrderedDict(
                force=args.force,
                label=args.label,
                isofile=args.isofile,
                usbfile=args.outfile,
                verbose=args.verbose,
                fstype=args.fstype,
                quiet=args.quiet,
                indent_base=("    " if args.verbose else "")
        ))

        if args.canned:
            if args.canned == 'centos7':
                print("Iniiting with params : " + str(params))
                builder = Centos7ImageBuilder(**params)
            elif args.canned == 'minimal':
                builder = DoNothingImageBuilder(**params)
            elif args.canned == 'defaults':
                builder = CopyFilesImageBuilder(**params)
            elif args.canned == 'iso-based':
                builder = IsoBasedImageBuilder(**params)
        else:
            builder = ImageBuilder(**params)

        if args.clear:
            builder.clear_actions()

        if args.copy:
            for item in args.copy:
                builder.add_action(CopyAction(item))

        inspect_action = None

        if args.inspect:
            if args.sed:
                inspect_action=PatchFileAction(args.inspect, *args.sed)
            else:
                inspect_action=InspectAction(args.inspect)

        main_result=None

        if args.update:
            if inspect_action:
                builder.add_action(inspect_action)
            main_result = builder.update_mode()
        elif args.create:
            if inspect_action:
                builder.add_action(inspect_action)
            main_result = builder.build_mode(size=args.size)
        elif args.inspect:
            print("inspect")
            main_result = builder.inspect_mode()
            if main_result:
                if inspect_action:
                    print("Perform inspection action")
                    inspect_result =builder.perform_actions([inspect_action])
                    main_result = main_result and inspect_result
        if not main_result:
            print("Failed",file=sys.stderr)
        return main_result

    except ValueError as ve:
        print(ve)
        if args.debug:
            traceback.print_exc()
        return False
    finally:
        if builder:
            builder.cleanup()

def main(argv):
    parser = argparse.ArgumentParser()

    parser.add_argument('--out',     '-o', dest='outfile', type=str)
    parser.add_argument('--iso',     '-i', dest='isofile', type=str)
    parser.add_argument('--size',    '-s', dest='size',    type=int)
    parser.add_argument('--label',   '-l', dest='label',   type=str)
    parser.add_argument('--fstype',  '-f', dest='fstype',  type=str)
    parser.add_argument('--force',         dest='force',   action='store_true')
    parser.add_argument('--create',  '-c', dest='create',  action='store_true')
    parser.add_argument('--update',  '-u', dest='update',  action='store_true')
    parser.add_argument('--patch',   '-p', dest='patch',   action='store_true')
    parser.add_argument('--clear',         dest='clear',   action='store_true')
    parser.add_argument('--debug',         dest='debug',   action='store_true')
    parser.add_argument('--quiet',         dest='quiet',   action='store_true')
    parser.add_argument('--verbose', '-v', dest='verbose', action='count', default=0)
    parser.add_argument('--edit',          dest='inspect', type=str)
    parser.add_argument('--inspect',       dest='inspect', type=str)
    parser.add_argument('--sed',           dest='sed',     nargs=3,type=str)
    parser.add_argument('--copy',          dest='copy',     nargs='*',type=str)
    parser.add_argument('--centos7',       dest='canned',  const='centos7',action='store_const')
    parser.add_argument('--defaults',      dest='canned',  const='defaults',action='store_const')
    parser.add_argument('--minimal',       dest='canned',  const='minimal',action='store_const')

    args = parser.parse_args(argv)
    
    if not (args.outfile and args.isofile):
        parser.print_help()
        sys.exit(1)

    run(args)

if __name__ == "__main__":
    main(sys.argv[1:])
