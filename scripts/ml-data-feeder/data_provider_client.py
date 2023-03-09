#!/usr/bin/env python3.8
# SPDX-License-Identifier: BSD-3-Clause
# -*- coding: utf-8 -*-

"""
Copyright 2022 NXP
"""

import logging
import socket
import struct
import sys
from time import sleep

import numpy as np

# Setup logging to stdout.
LOGGER = logging.getLogger(__name__)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

FLOAT_PRECISION = 6

class DataProviderClient():
    """
    Implements a client which provides input data and label for a
    machine learning model running on the board.
    """

    def __init__(self, data_sequence, board_ip, port, time_step=1):
        """
        :param data_sequence: Iterable sequence of (input_data, label) pairs.
        :param board_ip: Ip of the board where the receiver server resides.
        :param port: Port used for the eth connection.
        :param time_step: Time between subsequent sends.
        """
        self.data_sequence = data_sequence
        self.__board_ip = board_ip
        self.__port = port
        self.__time_step = time_step

        self.__socket = None

    def send_data(self, data, label=None, socket_timeout=1):
        """
        Send a pair of input data and label to the receiver server.
        :param data: The input data for the model.
        :param label: The real label for the input data.
        :param socket_timeout: Timeout for socket operations.
        """
        self.__socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if socket_timeout:
            self.__socket.settimeout(socket_timeout)

        try:
            # After a certain threshold the numpy array converts into a string
            # with gaps (0, 1 ... 10, 11). We need to set this threshold high enough
            # so we will get the whole data array as a string without this gap.
            # For each number we expect to have at most <FLOAT_PRECISION + 4> characters.
            # (The four comes from: one char for the dot, one for the single integer digit,
            # one for the space between numbers, and one for misc extra chars such as endl)
            threshold = data.size * (FLOAT_PRECISION + 4)

            # Convert the numpy array to string and get rid of the consecutive whitespaces.
            payload = ' '.join(np.array2string(
                data.flatten(), precision=FLOAT_PRECISION,
                floatmode='fixed', suppress_small=True, threshold=threshold)[1:-1].split()).encode()

            payload_size = struct.pack("i", len(payload))

            # Connection might not be stable, hence we connect every time for higher reliability
            self.__socket.connect((self.__board_ip, self.__port))
            # Send the input data
            self.__socket.sendall(payload_size)
            self.__socket.sendall(payload)

            # Send the label
            if label:
                payload = str(label).encode()
                payload_size = struct.pack("i", len(payload))

                self.__socket.sendall(payload_size)
                self.__socket.sendall(payload)
        # pylint: disable=broad-except
        except Exception as exception:
            LOGGER.error("Failed to send message to %s:%s\n%s",
                         self.__board_ip, self.__port, exception)
        finally:
            self.__socket.close()

    def send_all(self, count=None):
        """
        Send all (or a set number of) data-label pairs,
        with the given time step interval between sends.
        :param count: The number of inputs to send. Must be int or None.
        """
        for data, label in self.data_sequence:
            self.send_data(data, label)
            sleep(self.__time_step)

            if count and isinstance(count, int):
                count -= 1
                if count <= 0:
                    break
