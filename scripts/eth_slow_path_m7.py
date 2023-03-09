#!/usr/bin/env python3.8
# SPDX-License-Identifier: BSD-3-Clause
# -*- coding: utf-8 -*-

"""
Copyright 2021 NXP
"""

import argparse
import ctypes
import os
import random
import socket
import string
import threading
import time

libc = ctypes.CDLL('libc.so.6')

def netns_socket(nsname, *args):
    '''Create the socket inside a network namespace.
       :param nsname: name of the network namespace
       :param args: the arguments that are passed to py socket object
       :return: a socket opened in the desired network namespace
    '''
    with NetNS(nsname):
        return socket.socket(*args)

class NetNS ():
    '''A context manager for running code inside a network namespace.
    '''
    CLONE_NEWNET = 0x40000000

    def __init__(self, nsname):
        '''
           :param nsname: name of the network namespace
        '''
        self.path = self.get_pid_path(os.getpid())
        self.targetpath = self.get_netns_path(nsname)
        self.net_ns = None

    def __enter__(self):
        '''Saves the current namespace and open the target ns
        '''
        self.net_ns = open(self.path, encoding='utf-8')
        with open(self.targetpath, encoding='utf-8') as ns_fd:
            self.set_netns(ns_fd, self.CLONE_NEWNET)

    def __exit__(self, *args):
        '''Restore the original namespace
        '''
        self.set_netns(self.net_ns, self.CLONE_NEWNET)
        self.net_ns.close()

    @staticmethod
    def set_netns(net_fd, nstype):
        '''A wrapper for libc.setns
           :param net_fd: a file descriptor refering to a namespace
           :param nstype: indicates to what fd refers to (network namespace in this case)
        '''
        if hasattr(net_fd, 'fileno'):
            net_fd = net_fd.fileno()
        return libc.setns(net_fd, nstype)

    @staticmethod
    def get_netns_path(name):
        '''Return a filesystem path from a network namespace name.
           :param name: name of the network namespace
           :return: path in file system
        '''
        path = os.path.abspath(os.path.join(os.sep, 'var', 'run', 'netns', name))
        if not os.path.exists(path):
            raise NameError(f'Network namespace path {path} is invalid')
        return path

    @staticmethod
    def get_pid_path(pid):
        '''Return a filesystem path from a process id.
           :param pid: process id
           :return: path in file system
        '''
        return os.path.abspath(os.path.join(os.sep, 'proc', str(pid), 'ns', 'net'))

# pylint: disable=too-many-instance-attributes
class M7ETHBenchmark():
    '''Actually runs the ethernet benchmark. It does this by creating and managing the
       sockets through which the communication is done with the target.
       For each channel of communication it starts a pair of sending and receiving threads
       which run for the time passed as a argument.
       After the benchmark is done it gathers the data and computes the metrics(e.g. bandwidth)
    '''
    BITS_IN_MBIT = 1000000
    UDP_HEADER_SIZE = 42
    TCP_HEADER_SIZE = 56
    def __init__(self, config):
        '''
           :param config: the collection of parameters passed from command line
        '''
        self.__sock_ns = [ ]
        #create the two sockets and set a timeout on them so that recv will return eventually
        self.__sock_ns.append(netns_socket(config.ns0,socket.AF_INET,  # Internet
                                           SOCKET_TYPE))
        self.__sock_ns.append(netns_socket(config.ns1,socket.AF_INET,  # Internet
                                           SOCKET_TYPE))
        self.__sock_ns[0].settimeout(2.0)
        self.__sock_ns[1].settimeout(2.0)
        # Bind to interface

        self.__sock_ns[0].bind((config.host_ip_eth0, config.host_tcpip_port))
        self.__sock_ns[1].bind((config.host_ip_eth1, config.host_tcpip_port))
        #if the connection type is TCP connect to the target sockets and adjust the header size
        if config.conn_type == "TCP":
            self.header_size = self.TCP_HEADER_SIZE
            self.__sock_ns[1].connect((config.board_ip_eth1, config.board_tcpip_port))
            self.__sock_ns[0].connect((config.board_ip_eth0, config.board_tcpip_port))
        else:
            self.header_size = self.UDP_HEADER_SIZE
        # input message size
        self.__message_size = config.message_size
        # random message
        self.__message = ''.join(random.choice(string.ascii_lowercase) for i in range(
            self.__message_size - self.header_size))

        self.__timeout = config.timeout

        # Statistics
        self.__sent_packets_eth = [0, 0]
        self.__received_packets_eth = [0, 0]

        # synchronization variables for threads
        self.__run_sender = False
        self.__run_receiver = False

    def __send_packets(self, idx, send_address):
        '''Keep sending packets to the target until signaled by main thread to stop.
           :param idx: indicates the socket used
           :param send_address: a tuple that contains the ip and port number of target socket
        '''
        while self.__run_sender:
            try:
                self.__sock_ns[idx].sendto(self.__message.encode(), send_address)
                self.__sent_packets_eth[idx] += 1
            except socket.timeout:
                pass
            #send 10 packets every 1ms otherwise we might overrun the OS buffers
            time.sleep(1.0/10**4)

    def __receive_packets(self, idx):
        '''Keep trying to receive packets until signaled by main thread to stop.
           :param idx: indicates the used socket
        '''
        while self.__run_receiver:
            try:
                self.__sock_ns[idx].recvfrom(self.__message_size)
                self.__received_packets_eth[idx] += 1
            except socket.timeout:
                pass

    def print_test_results(self,idx):
        '''Computes the benchmark metrics and writes them to output log(s) file(s)
		   :param idx: indicates the ethernet path for which the metrics are computed and printed
        '''
        with open(cfg.logfile[idx], "w+", encoding='utf-8') as out_fd:
            sent_packets = self.__sent_packets_eth[idx]
            recv_packets = self.__received_packets_eth[idx-1]
            lost_packets = float(sent_packets - recv_packets) / float(sent_packets) * float(100)
            bandwidth = float(recv_packets * self.__message_size * 8) /\
                        float(self.__timeout * self.BITS_IN_MBIT)
            print(f"Writing results to {cfg.logfile[idx]}")
            out_fd.write(f"Test execution time {self.__timeout}\n")
            out_fd.write(f"Input packet size {self.__message_size}\n")
            out_fd.write(f"Injected packets {sent_packets}\n")
            out_fd.write(f"Received packets {recv_packets}\n")
            out_fd.write(f"Lost packets {lost_packets}%\n")
            # convert from Message (in bytes) to the Mbits/sec
            out_fd.write(f"Bandwidth: {bandwidth} Mbps\n")
            if not sent_packets:
                out_fd.write("No packets have been transmitted. Please check your connections\n")
            if not recv_packets:
                out_fd.write("No packets have been received. Please check your connections\n")

    def run_benchmark(self):
        '''Actually runs the benchmark. It spawns the sending/receiving threads and after this it
           sleeps for the length of the test. When it wakes up it indicates to the spawned threads
           to stop.
        '''
        print("Running Ethernet benchmark for M7 core")
        print("#######################################")
        self.__run_receiver = True
        self.__run_sender = True
        receiver_thread_eth1 = threading.Thread(target=self.__receive_packets,args=(1,))
        receiver_thread_eth1.start()
        if cfg.duplex == "full":
            receiver_thread_eth0 = threading.Thread(target=self.__receive_packets,args=(0,))
            receiver_thread_eth0.start()
        time.sleep(1)
        sender_thread_eth0 = threading.Thread(target=self.__send_packets,
                                              args=(0, (cfg.board_ip_eth0, cfg.board_tcpip_port)))
        sender_thread_eth0.start()
        if cfg.duplex == "full":
            sender_thread_eth1 = threading.Thread(target=self.__send_packets,
                                                  args=(1,
                                                  (cfg.board_ip_eth1, cfg.board_tcpip_port)))
            sender_thread_eth1.start()

        # Wait while the test is running
        time.sleep(self.__timeout)
        print("Test has finished, getting results")

        self.__run_sender = False
        # Wait for in-flight packets
        time.sleep(1)
        self.__run_receiver = False

        receiver_thread_eth1.join()
        if cfg.duplex == "full":
            receiver_thread_eth0.join()
            sender_thread_eth1.join()
        sender_thread_eth0.join()

        self.__sock_ns[0].close()
        self.__sock_ns[1].close()
        self.print_test_results(0)
        if cfg.duplex == "full":
            self.print_test_results(1)


if __name__ == '__main__':
    # Parses the arguments, determines the type of socket needed and passes this information to
    # the ethernet benchmark object.

    parser = argparse.ArgumentParser()
    parser.add_argument("-s", dest="message_size", type=int, default=1500, help="Size of payload")
    parser.add_argument("-board-ip-eth0", dest="board_ip_eth0", type=str, default="169.254.10.12",
                        help="The board ip of eth connection 0")
    parser.add_argument("-board-ip-eth1", dest="board_ip_eth1", type=str, default="169.254.12.12",
                        help="The board ip of eth connection 1")
    parser.add_argument("-board-tcpip-port", dest="board_tcpip_port", type=int, default=5678,
                        help="The TCP/IP of the board")
    parser.add_argument("-host-ip-eth0", dest="host_ip_eth0", type=str, default="169.254.10.10",
                        help="The host ip of eth connection 0")
    parser.add_argument("-host-ip-eth1", dest="host_ip_eth1", type=str, default="169.254.12.10",
                        help="The host ip of eth connection 1")
    parser.add_argument("-host-tcpip-port", dest="host_tcpip_port", type=int, default=5001,
                        help="The host TCP/IP port")
    parser.add_argument("-l", dest="timeout", type=int, default=30,
                        help="Time of the test in seconds")
    parser.add_argument("-t", dest="conn_type", type=str, default="UDP",
                        help="Connection type(TCP/UDP)")
    parser.add_argument("-ns0", dest="ns0", type=str, default="nw_ns0", help="Network namespace 0")
    parser.add_argument("-ns1", dest="ns1", type=str, default="nw_ns1", help="Network namespace 1")
    parser.add_argument("-log", dest="logfile", nargs=2, help="List of files for output log")
    parser.add_argument("-d", dest="duplex", type=str, default="half",
                        help="Duplex option (half/full)")

    cfg = parser.parse_args()

    if cfg.conn_type == "TCP":
        SOCKET_TYPE = socket.SOCK_STREAM
    else:
        SOCKET_TYPE = socket.SOCK_DGRAM
    M7ETHBenchmark(cfg).run_benchmark()
