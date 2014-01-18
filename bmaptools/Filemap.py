# Copyright (c) 2012 Intel, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.

"""
This module implements python implements a way to get file block. Two methods
are supported - the FIEMAP ioctl and the 'SEEK_HOLE / SEEK_DATA' features of
the file seek syscall. The former is implemented by the 'FilemapFiemap' class,
the latter is implemented by the 'FilemapSeek' class. Both classes provide the
same API. The 'filemap' function automatically selects which class can be used
and returns an instance of the class.
"""

# Disable the following pylint recommendations:
#   * Too many instance attributes (R0902)
# pylint: disable=R0902

import os
import struct
import array
import fcntl
from bmaptools import BmapHelpers


class Error(Exception):
    """
    A class for exceptions generated by this module. We currently support only
    one type of exceptions, and we basically throw human-readable problem
    description in case of errors.
    """
    pass


class _FilemapBase:
    """
    This is a base class for a couple of other classes in this module. This
    class simply performs the common parts of the initialization process: opens
    the image file, gets its size, etc.
    """

    def __init__(self, image):
        """
        Initialize a class instance. The 'image' argument is full path to the
        file or file object to operate on.
        """

        self._f_image_needs_close = False

        if hasattr(image, "fileno"):
            self._f_image = image
            self._image_path = image.name
        else:
            self._image_path = image
            self._open_image_file()

        try:
            self.image_size = os.fstat(self._f_image.fileno()).st_size
        except IOError as err:
            raise Error("cannot get information about file '%s': %s"
                        % (self._f_image.name, err))

        try:
            self.block_size = BmapHelpers.get_block_size(self._f_image)
        except IOError as err:
            raise Error("cannot get block size for '%s': %s"
                        % (self._image_path, err))

        self.blocks_cnt = self.image_size + self.block_size - 1
        self.blocks_cnt /= self.block_size

        try:
            self._f_image.flush()
        except IOError as err:
            raise Error("cannot flush image file '%s': %s"
                        % (self._image_path, err))

        try:
            os.fsync(self._f_image.fileno()),
        except OSError as err:
            raise Error("cannot synchronize image file '%s': %s "
                        % (self._image_path, err.strerror))

    def __del__(self):
        """The class destructor which just closes the image file."""
        if self._f_image_needs_close:
            self._f_image.close()

    def _open_image_file(self):
        """Open the image file."""
        try:
            self._f_image = open(self._image_path, 'rb')
        except IOError as err:
            raise Error("cannot open image file '%s': %s"
                        % (self._image_path, err))

        self._f_image_needs_close = True

    def block_is_mapped(self, block): # pylint: disable=W0613,R0201
        """
        This method has has to be implemented by child classes. It returns
        'True' if block number 'block' of the image file is mapped and 'False'
        otherwise.
        """

        raise Error("the method is not implemented")

    def block_is_unmapped(self, block): # pylint: disable=W0613,R0201
        """
        This method has has to be implemented by child classes. It returns
        'True' if block number 'block' of the image file is not mapped (hole)
        and 'False' otherwise.
        """

        raise Error("the method is not implemented")

    def get_mapped_ranges(self, start, count): # pylint: disable=W0613,R0201
        """
        This method has has to be implemented by child classes. This is a
        generator which yields ranges of mapped blocks in the file. The ranges
        are tuples of 2 elements: [first, last], where 'first' is the first
        mapped block and 'last' is the last mapped block.

        The ranges are yielded for the area of the file of size 'count' blocks,
        starting from block 'start'.
        """

        raise Error("the method is not implemented")

    def get_unmapped_ranges(self, start, count): # pylint: disable=W0613,R0201
        """
        This method has has to be implemented by child classes. Just like
        'get_mapped_ranges()', but yields unmapped block ranges instead
        (holes).
        """

        raise Error("the method is not implemented")

# The 'SEEK_HOLE' and 'SEEK_DATA' options of the file seek system call
_SEEK_DATA = 3
_SEEK_HOLE = 4

class FilemapSeek(_FilemapBase):
    """
    This class uses the 'SEEK_HOLE' and 'SEEK_DATA' to find file block mapping.
    """

    def __init__(self, image):
        """Refer the '_FilemapBase' class for the documentation."""

        # Call the base class constructor first
        _FilemapBase.__init__(self, image)

    def _lseek(self, file_obj, offset, whence):
        """This is a helper function which invokes 'os.lseek' for file object
        'file_obj' and with specified 'offset' and 'whence'. The 'whence'
        argument is supposed to be either '_SEEK_DATA' or '_SEEK_HOLE'. When
        there is no more data or hole starting from 'offset', this function
        returns '-1'.  Otherwise the data or hole position is returned."""

        try:
            return os.lseek(file_obj.fileno(), offset, whence)
        except OSError as err:
            # The 'lseek' system call returns the ENXIO if there is no data or
            # hole starting from the specified offset.
            if err.errno == os.errno.ENXIO:
                return -1
            else:
                raise

    def block_is_mapped(self, block):
        """Refer the '_FilemapBase' class for the documentation."""
        offs = self._lseek(self._f_image, block * self.block_size, _SEEK_DATA)
        if offs == -1:
            return False

        return offs / self.block_size == block

    def block_is_unmapped(self, block):
        """Refer the '_FilemapBase' class for the documentation."""
        return not self.block_is_mapped(block)

    def _get_ranges(self, start, count, whence1, whence2):
        """
        This function implements 'get_mapped_ranges()' and
        'get_unmapped_ranges()' depending on what is passed in the 'whence1'
        and 'whence2' arguments.
        """

        assert whence1 != whence2
        end = start * self.block_size
        limit = end + count * self.block_size

        while True:
            start = self._lseek(self._f_image, end, whence1)
            if start == -1 or start >= limit or start == self.image_size:
                break

            end = self._lseek(self._f_image, start, whence2)
            if end == -1 or end == self.image_size:
                end = self.blocks_cnt * self.block_size
            if end > limit:
                end = limit

            yield (start / self.block_size, end / self.block_size - 1)

    def get_mapped_ranges(self, start, count):
        """Refer the '_FilemapBase' class for the documentation."""
        return self._get_ranges(start, count, _SEEK_DATA, _SEEK_HOLE)

    def get_unmapped_ranges(self, start, count):
        """Refer the '_FilemapBase' class for the documentation."""
        return self._get_ranges(start, count, _SEEK_HOLE, _SEEK_DATA)


# Below goes the FIEMAP ioctl implementation, which is not very readable
# because it deals with the rather complex FIEMAP ioctl. To understand the
# code, you need to know the FIEMAP interface, which is documented in the
# "Documentation/filesystems/fiemap.txt" file in the Linux kernel sources.

# Format string for 'struct fiemap'
_FIEMAP_FORMAT = "=QQLLLL"
# sizeof(struct fiemap)
_FIEMAP_SIZE = struct.calcsize(_FIEMAP_FORMAT)
# Format string for 'struct fiemap_extent'
_FIEMAP_EXTENT_FORMAT = "=QQQQQLLLL"
# sizeof(struct fiemap_extent)
_FIEMAP_EXTENT_SIZE = struct.calcsize(_FIEMAP_EXTENT_FORMAT)
# The FIEMAP ioctl number
_FIEMAP_IOCTL = 0xC020660B
# This FIEMAP ioctl flag which instructs the kernel to sync the file before
# reading the block map
_FIEMAP_FLAG_SYNC = 0x00000001
# Size of the buffer for 'struct fiemap_extent' elements which will be used
# when invoking the FIEMAP ioctl. The larger is the buffer, the less times the
# FIEMAP ioctl will be invoked.
_FIEMAP_BUFFER_SIZE = 256 * 1024

class FilemapFiemap(_FilemapBase):
    """
    This class provides API to the FIEMAP ioctl. Namely, it allows to iterate
    over all mapped blocks and over all holes.

    This class synchronizes the image file every time it invokes the FIEMAP
    ioctl in order to work-around early FIEMAP implementation kernel bugs.
    """

    def __init__(self, image):
        """
        Initialize a class instance. The 'image' argument is full the file
        object to operate on.
        """

        # Call the base class constructor first
        _FilemapBase.__init__(self, image)

        self._buf_size = _FIEMAP_BUFFER_SIZE

        # Calculate how many 'struct fiemap_extent' elements fit the buffer
        self._buf_size -= _FIEMAP_SIZE
        self._fiemap_extent_cnt = self._buf_size / _FIEMAP_EXTENT_SIZE
        assert self._fiemap_extent_cnt > 0
        self._buf_size = self._fiemap_extent_cnt * _FIEMAP_EXTENT_SIZE
        self._buf_size += _FIEMAP_SIZE

        # Allocate a mutable buffer for the FIEMAP ioctl
        self._buf = array.array('B', [0] * self._buf_size)

        # Check if the FIEMAP ioctl is supported
        self.block_is_mapped(0)

    def _invoke_fiemap(self, block, count):
        """
        Invoke the FIEMAP ioctl for 'count' blocks of the file starting from
        block number 'block'.

        The full result of the operation is stored in 'self._buf' on exit.
        Returns the unpacked 'struct fiemap' data structure in form of a python
        list (just like 'struct.upack()').
        """

        if self.blocks_cnt != 0 and (block < 0 or block >= self.blocks_cnt):
            raise Error("bad block number %d, should be within [0, %d]"
                        % (block, self.blocks_cnt))

        # Initialize the 'struct fiemap' part of the buffer. We use the
        # '_FIEMAP_FLAG_SYNC' flag in order to make sure the file is
        # synchronized. The reason for this is that early FIEMAP
        # implementations had many bugs related to cached dirty data, and
        # synchronizing the file is a necessary work-around.
        struct.pack_into(_FIEMAP_FORMAT, self._buf, 0, block * self.block_size,
                         count * self.block_size, _FIEMAP_FLAG_SYNC, 0,
                         self._fiemap_extent_cnt, 0)

        try:
            fcntl.ioctl(self._f_image, _FIEMAP_IOCTL, self._buf, 1)
        except IOError as err:
            # Note, the FIEMAP ioctl is supported by the Linux kernel starting
            # from version 2.6.28 (year 2008).
            raise Error("the FIEMAP ioctl failed for '%s': %s"
                        % (self._image_path, err))

        return struct.unpack(_FIEMAP_FORMAT, self._buf[:_FIEMAP_SIZE])

    def block_is_mapped(self, block):
        """Refer the '_FilemapBase' class for the documentation."""
        struct_fiemap = self._invoke_fiemap(block, 1)

        # The 3rd element of 'struct_fiemap' is the 'fm_mapped_extents' field.
        # If it contains zero, the block is not mapped, otherwise it is
        # mapped.
        return bool(struct_fiemap[3])

    def block_is_unmapped(self, block):
        """Refer the '_FilemapBase' class for the documentation."""
        return not self.block_is_mapped(block)

    def _unpack_fiemap_extent(self, index):
        """
        Unpack a 'struct fiemap_extent' structure object number 'index' from
        the internal 'self._buf' buffer.
        """

        offset = _FIEMAP_SIZE + _FIEMAP_EXTENT_SIZE * index
        return struct.unpack(_FIEMAP_EXTENT_FORMAT,
                             self._buf[offset : offset + _FIEMAP_EXTENT_SIZE])

    def _do_get_mapped_ranges(self, start, count):
        """
        Implements most the functionality for the  'get_mapped_ranges()'
        generator: invokes the FIEMAP ioctl, walks through the mapped extents
        and yields mapped block ranges. However, the ranges may be consecutive
        (e.g., (1, 100), (100, 200)) and 'get_mapped_ranges()' simply merges
        them.
        """

        block = start
        while block < start + count:
            struct_fiemap = self._invoke_fiemap(block, count)

            mapped_extents = struct_fiemap[3]
            if mapped_extents == 0:
                # No more mapped blocks
                return

            extent = 0
            while extent < mapped_extents:
                fiemap_extent = self._unpack_fiemap_extent(extent)

                # Start of the extent
                extent_start = fiemap_extent[0]
                # Starting block number of the extent
                extent_block = extent_start / self.block_size
                # Length of the extent
                extent_len = fiemap_extent[2]
                # Count of blocks in the extent
                extent_count = extent_len / self.block_size

                # Extent length and offset have to be block-aligned
                assert extent_start % self.block_size == 0
                assert extent_len % self.block_size == 0

                if extent_block > start + count - 1:
                    return

                first = max(extent_block, block)
                last = min(extent_block + extent_count, start + count) - 1
                yield (first, last)

                extent += 1

            block = extent_block + extent_count

    def get_mapped_ranges(self, start, count):
        """Refer the '_FilemapBase' class for the documentation."""
        iterator = self._do_get_mapped_ranges(start, count)

        first_prev, last_prev = iterator.next()

        for first, last in iterator:
            if last_prev == first - 1:
                last_prev = last
            else:
                yield (first_prev, last_prev)
                first_prev, last_prev = first, last

        yield (first_prev, last_prev)

    def get_unmapped_ranges(self, start, count):
        """Refer the '_FilemapBase' class for the documentation."""
        hole_first = start
        for first, last in self._do_get_mapped_ranges(start, count):
            if first > hole_first:
                yield (hole_first, first - 1)

            hole_first = last + 1

        if hole_first < start + count:
            yield (hole_first, start + count - 1)


def filemap(image):
    """
    Create and return an instance of a Filemap class - 'FilemapFiemap' or
    'FilemapSeek', depending on what the system we run on supports. If the
    FIEMAP ioctl is supported, an instance of the 'FilemapFiemap' class is
    returned. Otherwise, if 'SEEK_HOLE' is supported an instance of the
    'FilemapSeek' class is returned. If none of these are supported, the
    function generates an 'Error' type exception.
    """

    try:
        return FilemapFiemap(image)
    except Error:
        return FilemapSeek(image)