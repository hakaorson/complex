from Data import data
from Model import models
from Model import flow
import random
import torch
import time
import datetime
import pickle
import logging


def regression():
    random.seed(666)
    tarindatas, datasets_name = data.trainmodel_datasets(
        basedir="Data/", recompute=False, typ="regression", refername="clique_percolation")
    datas = [[item.graph, item.feat, item.label] for item in tarindatas]
    random.shuffle(datas)
    size = len(datas)
    cut1 = int(0.8*size)
    traindatas, valdatas = datas[:cut1], datas[cut1:]

    model_regression = models.GCN_with_Topologi(
        nodefeatsize=486,
        edgefeatsize=19,
        graphfeatsize=10,
        hidden_size=128,
        gcn_layers=2,
        output_size=1,
        activate=torch.nn.Sigmoid()
    )
    model_path = "Model/saved_models/{}_{}_{}_regression".format(model_regression.name, datasets_name,
                                                                 time.strftime('{}{}{}{}'.format(time.localtime().tm_mon, time.localtime().tm_mday, (time.localtime().tm_hour)+8, time.localtime().tm_min)))
    default_epoch = 50
    batchsize = 16
    flow.train_regression(model_regression, traindatas, valdatas,
                          batchsize, model_path, default_epoch)


def classification_process():
    random.seed(666)
    tarindatas, datasets_name = data.trainmodel_datasets(
        basedir="Data/", recompute=True, typ="classification", refername="coach", benchname="CYC2008", graphname="DIP")
    datas = [[item.graph, item.feat, item.label] for item in tarindatas]
    random.shuffle(datas)
    size = len(datas)
    cut1 = int(0.8*size)
    traindatas, valdatas = datas[:cut1], datas[cut1:]
    model = models.GCN_with_Topologi(
        nodefeatsize=66,
        edgefeatsize=19,
        graphfeatsize=10,
        hidden_size=128,
        gcn_layers=2,
        output_size=5,
        activate=None
    )
    model_path = "Model/saved_models/{}_{}_{}".format(model.name, datasets_name,
                                                      time.strftime('{}{}{}{}'.format(time.localtime().tm_mon, time.localtime().tm_mday, (time.localtime().tm_hour)+8, time.localtime().tm_min)))
    default_epoch = 50
    batchsize = 16
    # flow.train_classification(model, traindatas, valdatas,
    #                           batchsize, model_path, default_epoch)
    flow.train_classification(model, traindatas, valdatas,
                              batchsize, model_path, default_epoch)


if __name__ == "__main__":
    # regression()
    classification_process()
