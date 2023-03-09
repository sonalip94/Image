#!/usr/bin/env python3.8
# SPDX-License-Identifier: BSD-3-Clause
# -*- coding: utf-8 -*-

"""
Copyright 2022 NXP
"""

import argparse
import os
import pickle

import pandas as pd
import numpy as np

from sklearn.preprocessing import StandardScaler

from data_provider_client import DataProviderClient

# Default path to the predictive maintenance validation dataset.
DEFAULT_DATA_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'BatterryMgmtSystemValidation.csv')
# Default path to the StandardScaler model used for training the BMS model.
DEFAULT_SCALER_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'bms_scaler.pkl')
# Window size used to shape the input data sequences. Should match the window size used to train the neural network.
WINDOW_SIZE = 70


def windowed_dataset(df, window_size):
    """
    Convert one dataset in matrices of dimension (window_size, nb_features) as input for the Neural Network.
    :param df: the dataset as a numpy array
    :param window_size: window size
    :return: tuple(numpy array containing input sequences, numpy array with the labels)
    """
    sequences, labels = list(), list()

    for i in range(df.shape[0]):
        end_ix = i + window_size
        if end_ix > df.shape[0] - 1:
            break

        window_seq, window_label = df[i:end_ix, :-1], df[end_ix-1, -1]
        sequences.append(window_seq)
        labels.append(window_label)

    return np.array(sequences), np.array(labels)


def add_mean_variance(seq_array, feature_col_idx, window_size):
    """
    Add the mean and variance of an input in the data sequence of the input.
    :param seq_array: numpy array shaped as (window_size, nb_features) containing the input sequences
    :param feature_col_idx: index of the feature which is considered in order to compute mean and variance
    :param window_size: window size

    :return: numpy array containing the altered input sequences
    """
    new_seq_array = np.zeros((seq_array.shape[0], seq_array.shape[1], seq_array.shape[2] + 2))

    for idx, sample in enumerate(seq_array):
        f_mean = np.array([sample[:, feature_col_idx].mean()] * window_size)
        f_var = np.array([sample[:, feature_col_idx].var()] * window_size)
        f_mean = f_mean.reshape(-1, 1)
        f_var = f_var.reshape(-1, 1)

        stat = np.concatenate((f_mean, f_var), axis=1)
        new_seq_array[idx] = np.concatenate((sample, stat), axis=1)

    return new_seq_array

def bms_get_data(data_path, scaler_path):
    """
    Parse the data file to generate the input data for the battery management system model.
    The BMS model receives as input a number of consecutive sets of sensor readings, while the
    output of the model is the predicted state of charge (SoC). Besides each input data, the real
    SoC is provided after the sequence of sensor readings.

    :param data_path: Path to the csv file containing the BMS data.
    :param scaler_path: Path to the scaler model to be used for normalizing the dataset.
    """
    test_df = pd.read_csv(data_path)

    # Load the scaler model used to normalize the training dataset. This shall be updated with
    # the corresponding scaler model if another trained neural network is used.
    with open(scaler_path, 'rb') as scaler_model_file:
        scaler = pickle.load(scaler_model_file)

    # Preprocessing the dataset - rename some columns in order to have more representative names.
    # This might be not needed if another dataset is used.
    for i in range(1, 7):
        test_df.rename(columns={"{}".format(-i): 'Temp_cell_{}'.format(i)}, inplace=True)
        test_df.rename(columns={"{}".format(-i * 100): 'Cell_voltage_{}'.format(i)}, inplace=True)

    # Keep 3 features from the dataset, along with the label.
    columns = ['Pack_Current', 'Cell_voltage_1', 'Temp_cell_1', 'TrueSOC1']

    test_df = test_df[columns][test_df["Time"] % 1 == 0]

    # Normalization.
    test_df.iloc[:, :-1] = scaler.transform(test_df.iloc[:, :-1])
    test_df = np.array(test_df)

    seq_array, label_array = windowed_dataset(test_df, WINDOW_SIZE)
    seq_array = add_mean_variance(seq_array, 1, WINDOW_SIZE)

    return ((seq, soc) for seq, soc in zip(seq_array, label_array))


def main():
    """
    Send all (or a set number of) data inputs to the board.
    """
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter,
                                     description='')
    parser.add_argument("--data-file", type=str, default=DEFAULT_DATA_FILE,
                        help="Path to the data csv file.")
    parser.add_argument("--scaler-file", type=str, default=DEFAULT_SCALER_PATH,
                        help="Path to the scaler model.")
    parser.add_argument("--board-ip", required=True, help="Ip of the S32G board.")
    parser.add_argument("--board-port", type=int, default=51003,
                        help="Port used by the BMS app on the S32G board.")
    parser.add_argument("--time-step", type=float, default=1,
                        help="Time step between data sends.")
    parser.add_argument("--stop-after", type=int, default=None,
                        help="Only send a set number of data inputs.")
    args = parser.parse_args()

    DataProviderClient(
        data_sequence=bms_get_data(args.data_file, args.scaler_file),
        time_step=args.time_step,
        port=args.board_port,
        board_ip=args.board_ip).send_all(args.stop_after)


if __name__ == '__main__':
    main()
