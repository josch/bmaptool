# -*- coding: utf-8 -*-
# vim: ts=4 sw=4 et ai si
#
# Copyright (c) 2012-2014 Intel, Inc.
# License: GPLv2
# Author: Artem Bityutskiy <artem.bityutskiy@linux.intel.com>
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
This test verifies 'Filemap' module functionality. It generates random sparse
files and makes sure the module returns correct information about the holes.
"""

# Disable the following pylint recommendations:
#   *  Too many public methods - R0904
#   *  Too many arguments - R0913
# pylint: disable=R0904
# pylint: disable=R0913

import sys
import random
import itertools
import tests.helpers
from six.moves import zip_longest
from bmaptools import Filemap

# This is a work-around for Centos 6
try:
    import unittest2 as unittest  # pylint: disable=F0401
except ImportError:
    import unittest


class Error(Exception):
    """A class for exceptions generated by this test."""

    pass


def _check_ranges(f_image, filemap, first_block, blocks_cnt, ranges, ranges_type):
    """
    This is a helper function for '_do_test()' which compares the correct
    'ranges' list of mapped or unmapped blocks ranges for file object 'f_image'
    with what the 'Filemap' module reports. The 'ranges_type' argument defines
    whether the 'ranges' list is a list of mapped or unmapped blocks. The
    'first_block' and 'blocks_cnt' define the subset of blocks in 'f_image'
    that should be verified by this function.
    """

    if ranges_type == "mapped":
        filemap_iterator = filemap.get_mapped_ranges(first_block, blocks_cnt)
    elif ranges_type == "unmapped":
        filemap_iterator = filemap.get_unmapped_ranges(first_block, blocks_cnt)
    else:
        raise Error("incorrect list type")

    last_block = first_block + blocks_cnt - 1

    # The 'ranges' list contains all ranges, from block zero to the last
    # block. However, we are conducting a test for 'blocks_cnt' of blocks
    # starting from block 'first_block'. Create an iterator which filters
    # those block ranges from the 'ranges' list, that are out of the
    # 'first_block'/'blocks_cnt' file region.
    ranges_iterator = (x for x in ranges if x[1] >= first_block and x[0] <= last_block)
    iterator = zip_longest(ranges_iterator, filemap_iterator)

    # Iterate over both - the (filtered) 'ranges' list which contains correct
    # ranges and the Filemap generator, and verify the mapped/unmapped ranges
    # returned by the 'Filemap' module.
    for correct, check in iterator:

        # The first and the last range of the filtered 'ranges' list may still
        # be out of the limit - correct them in this case
        if correct[0] < first_block:
            correct = (first_block, correct[1])
        if correct[1] > last_block:
            correct = (correct[0], last_block)

        if check[0] > check[1] or check != correct:
            raise Error(
                "bad or unmatching %s range for file '%s': correct "
                "is %d-%d, get_%s_ranges(%d, %d) returned %d-%d"
                % (
                    ranges_type,
                    f_image.name,
                    correct[0],
                    correct[1],
                    ranges_type,
                    first_block,
                    blocks_cnt,
                    check[0],
                    check[1],
                )
            )

        for block in range(correct[0], correct[1] + 1):
            if ranges_type == "mapped" and filemap.block_is_unmapped(block):
                raise Error(
                    "range %d-%d of file '%s' is mapped, but"
                    "'block_is_unmapped(%d) returned 'True'"
                    % (correct[0], correct[1], f_image.name, block)
                )
            if ranges_type == "unmapped" and filemap.block_is_mapped(block):
                raise Error(
                    "range %d-%d of file '%s' is unmapped, but"
                    "'block_is_mapped(%d) returned 'True'"
                    % (correct[0], correct[1], f_image.name, block)
                )


def _do_test(f_image, filemap, mapped, unmapped):
    """
    Verify that the 'Filemap' module provides correct mapped and unmapped areas
    for the 'f_image' file object. The 'mapped' and 'unmapped' lists contain
    the correct ranges. The 'filemap' is one of the classed from the 'Filemap'
    module.
    """

    # Check both 'get_mapped_ranges()' and 'get_unmapped_ranges()' for the
    # entire file.
    first_block = 0
    blocks_cnt = filemap.blocks_cnt
    _check_ranges(f_image, filemap, first_block, blocks_cnt, mapped, "mapped")
    _check_ranges(f_image, filemap, first_block, blocks_cnt, unmapped, "unmapped")

    # Select a random area in the file and repeat the test few times
    for _ in range(0, 10):
        first_block = random.randint(0, filemap.blocks_cnt - 1)
        blocks_cnt = random.randint(1, filemap.blocks_cnt - first_block)
        _check_ranges(f_image, filemap, first_block, blocks_cnt, mapped, "mapped")
        _check_ranges(f_image, filemap, first_block, blocks_cnt, unmapped, "unmapped")


class TestFilemap(unittest.TestCase):
    """
    The test class for this unit tests. Basically executes the '_do_test()'
    function for different sparse files.
    """

    def test(self):  # pylint: disable=R0201
        """
        The test entry point. Executes the '_do_test()' function for files of
        different sizes, holes distribution and format.
        """

        # Delete all the test-related temporary files automatically
        delete = True
        # Create all the test-related temporary files in current directory
        directory = "."
        # Maximum size of the random files used in this test
        max_size = 16 * 1024 * 1024

        iterator = tests.helpers.generate_test_files(max_size, directory, delete)
        for f_image, _, mapped, unmapped in iterator:
            try:
                fiemap = Filemap.FilemapFiemap(f_image)
                _do_test(f_image, fiemap, mapped, unmapped)

                seek = Filemap.FilemapSeek(f_image)
                _do_test(f_image, seek, mapped, unmapped)
            except Filemap.ErrorNotSupp:
                pass
