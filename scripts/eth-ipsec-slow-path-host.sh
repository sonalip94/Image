#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# Copyright 2022 NXP
#
# This script implements the host machine logic for the IPsec slow path scenario.
#
# The IPsec slow path scenario demonstrates that the connection between the host and the target
# can be secured using strongSwan.

# shellcheck source=docker/scripts/eth-common-host.sh
source "$(dirname "${BASH_SOURCE[0]}")/eth-common-host.sh"

readonly SSH_COMMON_ARGS=("-oStrictHostKeyChecking=no" "-oUserKnownHostsFile=/dev/null" "-q")

# IPs and network interfaces to be configured. The host network interface is passed as a parameter
# in CLI (check host_netif variable).
readonly HOST_IP="10.0.101.1"
readonly TARGET_IP="10.0.101.2"
readonly TARGET_NETIF="pfe2"

# IPsec related configurations.
readonly IPSEC_CONN_NAME="goldvip-h2h"
readonly IPSEC_CONF_TEMPLATE="
conn ${IPSEC_CONN_NAME}
    ikelifetime=60m
    keylife=20m
    rekeymargin=3m
    keyingtries=1
    mobike=no
    keyexchange=ikev2
    left=LEFT_IP_PLACEHOLDER
    leftcert=LEFT_CERT_PLACEHOLDER
    leftid=LEFT_ID_PLACEHOLDER
    leftfirewall=yes
    right=RIGHT_IP_PLACEHOLDER
    rightid=RIGHT_ID_PLACEHOLDER
    type=IPSEC_TYPE_PLACEHOLDER
    auto=add
"

# Paths were the iperf3 logs are saved.
readonly iperf_sender_log="/tmp/iperf3_sender_$(date +%H_%M_%S).log"
readonly iperf_receiver_log="/tmp/iperf3_receiver_$(date +%H_%M_%S).log"

# The type of IPsec connection (transport / tunnel).
ipsec_conn_type="transport"

# Print the help message.
usage() {
    echo -e "Usage: sudo ./$(basename "$0") [option] <eth_interface>
Configure strongSwan on both the host and the target to secure the connection between them through
IPsec.
eth_interface: Host Ethernet interface connected to PFE2 port.

OPTIONS:
        -m <ipsec_conn_type>    the type of the IPsec connection
                                ipsec_conn_type=tunnel
                                               =transport (default)
        -t <stream_type>        specify the test to perform
                                stream_type=TCP
                                           =UDP (default)
        -d <duplex>             select duplex option
                                duplex=half for half-duplex
                                       full for full-duplex (default)
        -l <seconds>            specify the duration of the test (default is 30 seconds)
        -s <bytes>              TCP or UDP payload size (default is 1472/32k bytes)
        -u <tty_device>         UART device connected to target (default is /dev/ttyUSB0)
        -h|--help               help"
}

# Validate the network interface received as argument.
_check_eth_args() {
    local netif="$1"

    if [ ! -e /sys/class/net/"${netif}" ]; then
        echo -e "Wrong network interface, expected member of the list:"
        ls /sys/class/net
        usage
        exit "${INVALID_USER_ARGUMENT_ERR}"
    fi
}

# Parse the passed parameters and validate them.
check_input() {
    # Check root privileges.
    if [ "${EUID}" -ne 0 ]; then
        echo "Please run as root!"
        usage
        exit "${PRIVILEGE_ERR}"
    fi

    if [ $# -eq 0 ]; then
        echo -e "Please input parameters!\n"
        usage
        exit "${INVALID_USER_ARGUMENT_ERR}"
    fi

    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help)
                usage
                exit
                ;;
            -m)
                shift
                ipsec_conn_type="$1"
                if [ "${ipsec_conn_type}" != "transport" ] && \
                     [ "${ipsec_conn_type}" != "tunnel" ]; then
                    echo "Wrong IPsec connection type!"
                    usage
                    exit ${INVALID_USER_ARGUMENT_ERR}
                fi
                ;;
            -t)
                shift
                stream_type="$1"
                if [ "${stream_type}" != "UDP" ] && [ "${stream_type}" != "TCP" ]; then
                    echo "Wrong stream type!"
                    usage
                    exit ${INVALID_USER_ARGUMENT_ERR}
                fi
                ;;
            -d)
                shift
                duplex="$1"
                if [ "${duplex}" != "half" ] && [ "${duplex}" != "full" ]; then
                    echo "Wrong duplex option!"
                    usage
                    exit ${INVALID_USER_ARGUMENT_ERR}
                fi
                ;;
            -l)
                shift
                duration="$1"
                if [[ ! ${duration} =~ ${integer_regex} ]]; then
                    echo "Invalid test duration! -l argument must be a positive integer."
                    usage
                    exit ${INVALID_USER_ARGUMENT_ERR}
                fi
                ;;
            -s)
                shift
                payload_size="$1"
                if [[ ! ${payload_size} =~ ${payload_size_regex} ]]; then
                    echo "Invalid payload size type!"
                    usage
                    exit ${INVALID_USER_ARGUMENT_ERR}
                fi
                ;;
            -u)
                shift
                uart_dev="$1"
                if ! [ -c "${uart_dev}" ]; then
                    echo "Wrong tty device!"
                    usage
                    exit "${INVALID_USER_ARGUMENT_ERR}"
                fi
                ;;
            *)
                if [ $# -eq 1 ]; then
                    host_netif="$1"
                    _check_eth_args "${host_netif}"
                else
                    echo "Wrong input option!"
                    usage
                    exit "${INVALID_USER_ARGUMENT_ERR}"
                fi
                ;;
        esac
        shift
    done

    # Assign iperf3 payload size default value if it remained unset. We must account for the
    # additional headers added by ESP for both transport and tunnel modes.
    if [ "${payload_size}" -eq "0" ]; then
        if [ "${stream_type}" == "TCP" ]; then
            payload_size="32k"
        elif [ "${ipsec_conn_type}" == "transport" ]; then
            # UDP in transport mode. Account only for the ESP headers and trailers. The final packet
            # size should equal MTU (1514).
            payload_size="1416"
        else
            # UDP in tunnel mode. Tunnel mode adds a new IP header along the ESP headers/trailers.
            payload_size="1396"
        fi
    fi
}

# Configure the network connection for both the target and the host.
setup_connections() {
    local ping_return=0

    echo "Configuring target's ${TARGET_NETIF} interface..."
    echo "ip link set ${TARGET_NETIF} up" > "${uart_dev}"
    # Wait a bit for network interface bring up.
    sleep 2
    echo "Set IP ${TARGET_IP} on target's ${TARGET_NETIF} interface."
    echo "ip addr flush dev ${TARGET_NETIF}" > "${uart_dev}"
    echo "ip addr add ${TARGET_IP}/24 dev ${TARGET_NETIF}" > "${uart_dev}"

    echo "Configuring ${host_netif} interface..."
    ip link set dev "${host_netif}" up
    echo "Set IP ${HOST_IP} on ${host_netif} interface."
    ip addr flush dev "${host_netif}"
    ip addr add "${HOST_IP}/24" dev "${host_netif}"

    echo -e "Checking the connection to the device...\n"
    ping -I "${host_netif}" -c 4 "${TARGET_IP}" || ping_return=$?

    if [ "${ping_return}" -ne 0 ]; then
        echo "Could not detect any connection between ${host_netif} and target's ${TARGET_NETIF}!"
        echo "ARP cache: $(ip neigh show dev "${host_netif}")"
        clean_up
        exit "${NET_ERR}"
    else
        echo -e "Network connected!"
    fi
}

# Create the CA, keys, the end entity certificates and the IPsec configuration that references them.
# These resources will be used to establish a connection secured with IPsec.
provision_ipsec_config() {
    local -r tmp_conf_dir="/tmp/ipsec-conf"
    local -r ca_key_path="${tmp_conf_dir}/ca-key.pem"
    local -r ca_cert_path="${tmp_conf_dir}/ca-cert.pem"
    local start_timestamp=""

    # Ensure that the generated certificates will be valid on the target as well - the date may be
    # set in the past. Get the date from the device and generate the certificates using the
    # "minimum" date. Thus, we avoid altering the system date.
    start_timestamp="$(printf "%s\n" "$(date +%s)" \
        "$(ssh "${SSH_COMMON_ARGS[@]}" root@"${TARGET_IP}" 'date +%s')" | sort -g | head -n1)"

    rm -rf "${tmp_conf_dir}"
    mkdir -p "${tmp_conf_dir}"

    # Create a self-signed Certificate Authority.
    ipsec pki --gen --type rsa --size 4096 --outform pem > "${ca_key_path}"
    ipsec pki --self --type rsa --ca --dn "O=NXP CN=GoldVIP root CA"  \
        --lifetime 3650 --dateform "%s" --not-before "${start_timestamp}" \
        --in "${ca_key_path}" --outform pem > "${ca_cert_path}"

    declare -a peers=("host" "target")
    declare -a peers_ips=("${HOST_IP}" "${TARGET_IP}")

    # For each peer (the host and the target) prepare the IPsec configuration files and the
    # certificates used for IKEV2. The following hierarchy is created:
    #  - /etc/ipsec.goldvip.secrets - contains the list of secrets (preshared keys, pointer to X.509
    #                                 certs) used for IKE/IPsec authentication
    #  - /etc/ipsec.d/goldvip_ipsec.conf - use-case specific IPsec configuration and connections
    #  - /etc/ipsec.d/{cacerts,certs,private} - directories that contains the certificates used by
    #                                           the keying daemon
    for (( i=0; i<${#peers[@]}; i++ )); do
        local peer="${peers[$i]}"
        local peer_conf_dir="${tmp_conf_dir}/${peer}"
        local ipsec_conf_dir="${peer_conf_dir}/ipsec.d"
        local peer_key_path="${ipsec_conf_dir}/private/${peer}-key.pem"
        local peer_cert_path="${ipsec_conf_dir}/certs/${peer}-cert.pem"
        local goldvip_ipsec_conf="${ipsec_conf_dir}/goldvip_ipsec.conf"

        mkdir -p "${ipsec_conf_dir}/"{"cacerts","certs","private"}

        # Generate a private key, then use it to issue a X.509 certificate.
        ipsec pki --gen --type rsa --size 4096 --outform pem > "${peer_key_path}"
        ipsec pki --issue --type priv --dn "O=NXP CN=${peer}" --san "${peer}" --outform pem \
            --cakey "${ca_key_path}" --cacert "${ca_cert_path}" --in "${peer_key_path}" \
            --lifetime 3650 --dateform "%s" --not-before "${start_timestamp}" > "${peer_cert_path}"
        # Save the CA certificate.
        cp "${ca_cert_path}" "${ipsec_conf_dir}/cacerts"

        # This configuration will be included by /etc/ipsec.secrets.
        echo ": RSA $(basename "${peer_key_path}")" > "${peer_conf_dir}/ipsec.goldvip.secrets"

        # This will be included by /etc/ipsec.conf.
        echo "${IPSEC_CONF_TEMPLATE}" > "${goldvip_ipsec_conf}"
        sed -i "s|LEFT_IP_PLACEHOLDER|${peers_ips[${i}]}|g" "${goldvip_ipsec_conf}"
        sed -i "s|LEFT_CERT_PLACEHOLDER|$(basename "${peer_cert_path}")|g" "${goldvip_ipsec_conf}"
        sed -i "s|LEFT_ID_PLACEHOLDER|${peer}|g" "${goldvip_ipsec_conf}"
        sed -i "s|RIGHT_IP_PLACEHOLDER|${peers_ips[$((1 - i))]}|g" "${goldvip_ipsec_conf}"
        sed -i "s|RIGHT_ID_PLACEHOLDER|${peers[$((1 - i))]}|g" "${goldvip_ipsec_conf}"
        sed -i "s|IPSEC_TYPE_PLACEHOLDER|${ipsec_conn_type}|g" "${goldvip_ipsec_conf}"
    done

    # Provision each peer with the generated configuration.
    cp -r "${tmp_conf_dir}"/host/* /etc/.
    scp -r "${SSH_COMMON_ARGS[@]}" "${tmp_conf_dir}"/target/* root@"${TARGET_IP}":/etc/.

    rm -rf "${tmp_conf_dir}"
    sync
}

# Restart the keying daemons and establish the secure connection.
establish_ipsec_connection() {
    echo -e "\nEstablishing the IPsec connection...\n"
    # The HSE crypto driver is compiled as a module. Due to its higher priority, the crypto
    # operations are offloaded to HSE. GoldVIP currently doesn't format the key catalog, therefore
    # the crypto operations offloaded to HSE will fail.
    echo "modprobe -r hse" > "${uart_dev}"
    echo "ipsec restart" > "${uart_dev}"
    ipsec restart
    sleep 2

    # Initiate the connection, then check whether it was succesfully established.
    ipsec up "${IPSEC_CONN_NAME}"
    ipsec status 2> /dev/null | grep "${IPSEC_CONN_NAME}.*ESTABLISHED.*${HOST_IP}.*${TARGET_IP}.*"
    ipsec status 2> /dev/null | grep "${IPSEC_CONN_NAME}.*INSTALLED.*${ipsec_conn_type^^}.*"
}

# Run iperf to measure the performance of the network between host and target when IPsec is used
# to secure the connection.
run_performance_test() {
    local iperf_stream=""

    echo -e "\nTurning on iperf3 server on target..."
    echo "iperf3 -s -D -1 -B ${TARGET_IP} -p 5678" > "${uart_dev}"
    echo "iperf3 -s -D -1 -B ${TARGET_IP} -p 5679" > "${uart_dev}"
    sleep 2

    echo -e "\nRunning sar on target to get CPU load..."
    echo "sar -P ALL 1 > ${sar_log} &" > "${uart_dev}"

    echo -e "\nRunning iperf3 for ${duration} seconds to measure network performance..."
    echo "Starting ${stream_type} stream..."
    if [ "${stream_type}" = "UDP" ]; then
        iperf_stream="-u"
    fi

    if [ "${duplex}" = "full" ]; then
        iperf3 -A 0,1 -4 "${iperf_stream}" -b 0 -l "${payload_size}" -t "${duration}" \
            -B "${HOST_IP}" --cport 5001 -c "${TARGET_IP}" -p 5678 > "${iperf_sender_log}" &
        iperf3 -A 2,3 -R -4 "${iperf_stream}" -b 0 -l "${payload_size}" -t "${duration}" \
            -B "${HOST_IP}" --cport 5002 -c "${TARGET_IP}" -p 5679 > "${iperf_receiver_log}" &
        wait
    else
        iperf3 -A 0,1 -4 "${iperf_stream}" -b 0 -l "${payload_size}" -t "${duration}" \
            -B "${HOST_IP}" --cport 5001 -c "${TARGET_IP}" -p 5678 > "${iperf_sender_log}"
        iperf3 -A 2,3 -R -4 "${iperf_stream}" -b 0 -l "${payload_size}" -t "${duration}" \
            -B "${HOST_IP}" --cport 5002 -c "${TARGET_IP}" -p 5679 > "${iperf_receiver_log}"
    fi

    echo -e "\nKill sar process on target."
    echo "pkill -2 sar && sync" > "${uart_dev}"

    # Wait until pkill gets executed on target.
    sleep 3

    echo -e "\nCopy ${sar_log} from target to host."
    scp "${SSH_COMMON_ARGS[@]}" root@"${TARGET_IP}":"${sar_log}" "${sar_log}"
}

# Print iperf and sar logs.
print_log() {
    echo -e "\nNetwork performance from host to target with IPsec (${ipsec_conn_type^^} mode):"
    cat "${iperf_sender_log}"
    echo -e "\nNetwork performance from target to host with IPsec (${ipsec_conn_type^^} mode):"
    cat "${iperf_receiver_log}"

    echo -e "\nTarget CPU load:"
    < "${sar_log}" grep "Average: "

    echo -e "\nLog file at:"
    ls "${iperf_sender_log}" "${iperf_receiver_log}" "${sar_log}"
    echo ""
}

# Overrides the one provided by eth-common-host.
_clean_host() {
    set +Ee
    trap - ERR

    _kill_iperf3
    ipsec stop
    ip addr flush dev "${host_netif}"
}

set_trap
check_input "$@"
setup_host_tty
_clean_target
setup_connections
provision_ipsec_config
establish_ipsec_connection
run_performance_test
print_log
clean_up
