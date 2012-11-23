""" This module implements python API for the FIEMAP ioctl. The FIEMAP ioctl
allows to find holes and mapped areas in a file. """

# Note, a lot of code in this module is not very readable, because it deals
# with the rather complex FIEMAP ioctl. To understand the code, you need to
# know the FIEMAP interface, which is documented in the
# Documentation/filesystems/fiemap.txt file in the Linux kernel sources.

import os
import struct
import array
import fcntl
import itertools
from bmaptools import BmapHelpers

class Error(Exception):
    """ A class for exceptions generated by this module. We currently support
    only one type of exceptions, and we basically throw human-readable problem
    description in case of errors. """
    pass

class Fiemap:
    """ This class provides API to the FIEMAP ioctl. Namely, it allows to
    iterate over all mapped blocks and over all holes. """

    def _open_image_file(self):
        """ Open the image file. """

        try:
            self._f_image = open(self._image_path, 'rb')
        except IOError as err:
            raise Error("cannot open image file '%s': %s" \
                        % (self._image_path, err))

        self._f_image_needs_close = True

    def __init__(self, image):
        """ Initialize a class instance. The 'image' argument is full path to
        the file to operate on, or a file object to operate on. """

        self._f_image_needs_close = False

        if hasattr(image, "fileno"):
            self._f_image = image
            self._image_path = image.name
        else:
            self._image_path = image
            self._open_image_file()

        self.image_size = os.fstat(self._f_image.fileno()).st_size

        try:
            self.block_size = BmapHelpers.get_block_size(self._f_image)
        except IOError as err:
            raise Error("cannot get block size for '%s': %s" \
                        % (self._image_path, err))

        self.blocks_cnt = self.image_size + self.block_size - 1
        self.blocks_cnt /= self.block_size

        # Synchronize the image file to make sure FIEMAP returns correct values
        try:
            self._f_image.flush()
        except IOError as err:
            raise Error("cannot flush image file '%s': %s" \
                        % (self._image_path, err))
        try:
            os.fsync(self._f_image.fileno()),
        except OSError as err:
            raise Error("cannot synchronize image file '%s': %s " \
                        % (self._image_path, err.strerror))

        # Check if the FIEMAP ioctl is supported
        self.block_is_mapped(0)

    def __del__(self):
        """ The class destructor which closes the opened files. """

        if self._f_image_needs_close:
            self._f_image.close()

    def block_is_mapped(self, block):
        """ This function returns 'True' if block number 'block' of the image
        file is mapped and 'False' otherwise. """

        # Prepare a 'struct fiemap' buffer which contains a single
        # 'struct fiemap_extent' element.
        struct_fiemap_format = "=QQLLLL"
        struct_size = struct.calcsize(struct_fiemap_format)
        buf = struct.pack(struct_fiemap_format,
                          block * self.block_size,
                          self.block_size, 0, 0, 1, 0)
        # sizeof(struct fiemap_extent) == 56
        buf += "\0"*56
        # Python strings are "immutable", meaning that python will pass a copy
        # of the string to the ioctl, unless we turn it into an array.
        buf = array.array('B', buf)

        try:
            fcntl.ioctl(self._f_image, 0xC020660B, buf, 1)
        except IOError as err:
            error_msg = "the FIBMAP ioctl failed for '%s': %s" \
                        % (self._image_path, err)
            if err.errno == os.errno.EPERM or err.errno == os.errno.EACCES:
                # The FIEMAP ioctl was added in kernel version 2.6.28 in 2008
                error_msg += " (looks like your kernel does not support FIEMAP)"

            raise Error(error_msg)

        res = struct.unpack(struct_fiemap_format, buf[:struct_size])
        # res[3] is the 'fm_mapped_extents' field of 'struct fiemap'. If it
        # contains zero, the block is not mapped, otherwise it is mapped.
        return bool(res[3])

    def block_is_unmapped(self, block):
        """ This function returns 'True' if block number 'block' of the image
        file is not mapped (hole) and 'False' otherwise. """

        return not self.block_is_mapped(block)

    def _get_ranges(self, test_func):
        """ Internal helper generator which produces list of mapped or unmapped
        blocks. The 'test_func' is a function object which tests whether a
        block is mapped or unmapped. """

        iterator = xrange(self.blocks_cnt)
        for key, group in itertools.groupby(iterator, test_func):
            if key:
                # Find the first and the last elements of the group
                first = group.next()
                last = first
                for last in group:
                    pass
                yield first, last

    def get_mapped_ranges(self):
        """ Generate ranges of mapped blocks in the file. """

        return self._get_ranges(self.block_is_mapped)

    def get_unmapped_ranges(self):
        """ Generate ranges of unmapped blocks in the file. """

        return self._get_ranges(self.block_is_unmapped)