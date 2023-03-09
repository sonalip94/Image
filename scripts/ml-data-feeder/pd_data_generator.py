#!/usr/bin/env python3.8
# SPDX-License-Identifier: BSD-3-Clause
# -*- coding: utf-8 -*-

"""
Copyright 2022 NXP
"""

import argparse
import os

import pandas as pd
import numpy as np

from data_provider_client import DataProviderClient

# Default path to the predictive maintenance validation dataset.
DEFAULT_DATA_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'PredictiveManteinanceEngineValidation.csv')

# The input data for the engine predictive maintainance model
# is a sequence of 50 engine cycles.
SEQUENCE_LENGTH = 50

def pd_get_data(data_path):
    """
    Parse the data folder to generate the input data for the predictive maintenance model.
    The predictive maintenance model receives as input a number of consecutive
    engine cycles, each with all the sensor readings of a cycle.
    The model's output is the predicted RUL (remaining unit lifetime),
    given as the remaining number of cycles until the engine breaks.
    Besides each input data we provide the real RUL after that sequence of engine cycles.

    :param data_path: Path to the csv folder containing the predictive maintenance data.
    """
    sequences = []
    labels = []
    test_df = pd.read_csv(data_path)

    # Pick the feature columns
    sensor_cols = ['s' + str(i) for i in range(1,22)]
    sequence_cols = ['setting1', 'setting2', 'setting3', 'cycle_norm']
    sequence_cols.extend(sensor_cols)

    for engine_id in test_df['id'].unique():
        # We get the cycles of a single engine
        engine_cycles = test_df[test_df['id']==engine_id]
        # Some engines have fewer than SEQUENCE_LENGTH cycles and therefore we can't use
        # them as inputs.
        if engine_cycles.shape[0] <= SEQUENCE_LENGTH:
            continue

        # We get a matrix with only the input values,
        data_matrix = engine_cycles[sequence_cols].values
        # and an array with only the RUL values.
        engine_cycles_rul = engine_cycles['RUL'].values

        num_elements = data_matrix.shape[0]

        # We generate the inputs for this engine.
        # If there are 60 cycles for an engine we will have 10 inputs:
        # Input 1 with cycles 1 2 ... 50
        # Input 2 with cycles 2 3 ... 51
        # ...
        # Input 10 with cycles 11 12 ... 60
        for start, stop in zip(range(0, num_elements-SEQUENCE_LENGTH),
                               range(SEQUENCE_LENGTH, num_elements)):
            sequences.append(data_matrix[start:stop, :])
            labels.append(engine_cycles_rul[stop])

    seq_array = np.array(sequences)
    label_array = np.array(labels)

    return ((seq, rul) for seq, rul in zip(seq_array, label_array))


def main():
    """
    Send all (or a set number of) data inputs to the board.
    """
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter,
                                     description='')
    parser.add_argument("--data-file", type=str, default=DEFAULT_DATA_FILE,
                        help="Path to the data csv file.")
    parser.add_argument("--board-ip", required=True, help="Ip of the S32G board.")
    parser.add_argument("--board-port", type=int, default=51002,
                        help="Port used by the predictive maintenance app on the S32G board.")
    parser.add_argument("--time-step", type=float, default=1,
                        help="Time step between data sends.")
    parser.add_argument("--stop-after", type=int, default=None,
                        help="Only send a set number of data inputs.")
    args = parser.parse_args()

    DataProviderClient(
        data_sequence=pd_get_data(args.data_file),
        time_step=args.time_step,
        port=args.board_port,
        board_ip=args.board_ip).send_all(args.stop_after)


if __name__ == '__main__':
    main()
