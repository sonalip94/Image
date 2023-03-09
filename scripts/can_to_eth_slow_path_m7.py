#!/usr/bin/env python3.8
# SPDX-License-Identifier: BSD-3-Clause
# -*- coding: utf-8 -*-

"""
Copyright 2021-2022 NXP
"""

import argparse
import socket

class M7CAN2ETHBenchmark():
    '''Actually runs the ethernet benchmark. It does this by creating and managing the
       socket through which the communication is done with the target.
       After the benchmark is done it gathers the data and computes the metrics.
    '''
    BITS_IN_KBIT = 1000
    def __init__(self, config):
        '''
           :param config: the collection of parameters passed from command line
        '''
        self.__message_size = config.message_size
        #create socket and set a timeout on them so that recv will return eventually
        self.__sock = socket.socket(socket.AF_INET,  # Internet
                                    socket.SOCK_DGRAM)

        # 5 seconds for canperf.sh in target running
        self.__sock.settimeout(config.timeout + 5)

        # Bind to interface
        self.__sock.bind((config.host_ip_eth, config.host_tcpip_port))
        self.__timeout = config.timeout
        self.__received_packets_eth = 0

        # synchronization variable for thread
        self.__run_receiver = False

    def __receive_packets(self):
        '''Keep trying to receive packets until signaled by main thread to stop.
        '''
        while self.__run_receiver:
            try:
                self.__sock.recvfrom(self.__message_size)
                self.__received_packets_eth += 1
            except socket.timeout:
                # If a socket timeout occurs that means that the board does not send packets
                # anymore and we can finish the thread
                self.__run_receiver = False

    def print_test_results(self):
        '''Computes the benchmark metrics and writes them to output log(s) file(s)
        '''
        with open(cfg.logfile, "w+", encoding='utf-8') as out_fd:
            recv_packets = self.__received_packets_eth
            recv_data_transfer = recv_packets * self.__message_size

            bandwidth = int((recv_data_transfer * 8) / (self.__timeout * self.BITS_IN_KBIT))
            out_fd.write(f"Rx frames:                {recv_packets}\n")
            out_fd.write(f"Rx data transfer:         {recv_data_transfer} bytes\n")
            out_fd.write(f"Rx frames/s:              {int(recv_packets / self.__timeout)}\n")
            out_fd.write(f"Rx throughput:            {bandwidth} Kbit/s\n")

    def run_benchmark(self):
        '''Actually runs the benchmark. It spawns the sending/receiving threads and after this it
           sleeps for the length of the test. When it wakes up it indicates to the spawned threads
           to stop.
        '''
        print("Running CAN to Ethernet benchmark for M7 core")
        print("#############################################")
        self.__run_receiver = True
        self.__receive_packets()

        self.__sock.close()
        print("Test has finished, getting results")
        self.print_test_results()

if __name__ == '__main__':
    # Parses the arguments to the ethernet benchmark object.

    parser = argparse.ArgumentParser()
    parser.add_argument("-s", dest="message_size", type=int, default=1500, help="Size of payload")
    parser.add_argument("-host-ip-eth", dest="host_ip_eth", type=str, default="169.254.12.10",
                        help="The host ip of eth connection")
    parser.add_argument("-host-tcpip-port", dest="host_tcpip_port", type=int, default=1024,
                        help="The host TCP/IP port")
    parser.add_argument("-l", dest="timeout", type=int, default=30,
                        help="Time of the test in seconds")
    parser.add_argument("-log", dest="logfile", help="Files for output log")
    cfg = parser.parse_args()

    M7CAN2ETHBenchmark(cfg).run_benchmark()
