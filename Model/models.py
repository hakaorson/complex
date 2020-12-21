from torch import nn as torchnn
import torch
import dgl
import dgl.nn as dglnn


class DGLInit(torchnn.Module):
    def __init__(self, innode_size, inedge_size, hidden_size):
        super().__init__()
        self.init_weight_node = torchnn.Linear(
            innode_size, hidden_size, bias=True)
        self.init_weight_edge = torchnn.Linear(
            inedge_size, hidden_size, bias=True)
        self.activate = lambda x: x

    def forward(self, dgl_data: dgl.DGLGraph):
        dgl_data.ndata['h'] = self.activate(self.init_weight_node(

            dgl_data.ndata['feat']))

        dgl_data.edata['h'] = self.activate(self.init_weight_edge(
            dgl_data.edata['feat']))
        return dgl_data


class My_GCN(torchnn.Module):
    def __init__(self, hidden_size):
        super().__init__()
        self.edgeflowweight = torchnn.Linear(
            hidden_size, hidden_size, bias=True)
        self.edgesrcweight = torchnn.Linear(
            hidden_size, hidden_size, bias=True)
        self.edgefinaleweight = torchnn.Linear(
            hidden_size*2, hidden_size, bias=True)
        self.nodeweight = torchnn.Linear(
            hidden_size*2, hidden_size, bias=True)
        self.batchnorm = torch.nn.BatchNorm1d(hidden_size)
        self.activate = torchnn.LeakyReLU()

    def msg_gcn(self, edge):
        # 先是点汇聚边信息，然后更新边信息
        old_edgefeat = edge.data['h']
        nodeflowfeat = self.edgeflowweight(
            edge.dst['h'] - edge.src['h'])+self.edgesrcweight(edge.src['h'])
        new_edgefeat = self.activate(self.edgefinaleweight(
            torch.cat([old_edgefeat, nodeflowfeat], -1)))
        new_edgefeat = self.batchnorm(new_edgefeat)
        edge.data['h'] = new_edgefeat
        return {'msg': old_edgefeat}

    def reduce_gcn(self, node):
        reduce = torch.mean(node.mailbox['msg'], 1)  # 箭头指向某一个节点。所有的箭头的信息汇聚到一起
        return {'reduce': reduce}

    def apply_gcn(self, node):
        new_nodefeat = self.nodeweight(
            torch.cat([node.data['reduce'], node.data['h']], -1))
        new_nodefeat = self.activate(new_nodefeat)
        new_nodefeat = self.batchnorm(new_nodefeat)
        return {'h': new_nodefeat}

    def forward(self, dgl_data: dgl.DGLGraph):
        dgl_data.update_all(self.msg_gcn, self.reduce_gcn, self.apply_gcn)
        return dgl_data


class GCN_process(torchnn.Module):
    def __init__(self, hidden_size, layer_num):
        super().__init__()
        self.GCNlayers = torchnn.ModuleList()
        for _ in range(layer_num):
            self.GCNlayers.append(My_GCN(hidden_size))

    def forward(self, dgl_data):
        for singlelayer in self.GCNlayers:
            dgl_data = singlelayer(dgl_data)
        return dgl_data


class GCN_readout_cat(torchnn.Module):
    def __init__(self, hidden_size):
        super().__init__()
        self.weight_node = torchnn.Linear(
            hidden_size*4, hidden_size, bias=True)
        self.activate = torchnn.LeakyReLU()

    def forward(self, dgl_data):
        dgl_feat = torch.cat([
            dgl.mean_nodes(dgl_data, 'h'),
            dgl.max_nodes(dgl_data, 'h'),
            dgl.mean_edges(dgl_data, 'h'),
            dgl.max_edges(dgl_data, 'h'),
        ], -1)
        dgl_predict = self.activate(self.weight_node(dgl_feat))
        return dgl_predict


class GCN_readout_max(torchnn.Module):
    def __init__(self, hidden_size):
        super().__init__()
        self.weight_node = torchnn.Linear(
            hidden_size*4, hidden_size, bias=True)
        self.activate = torchnn.LeakyReLU()

    def forward(self, dgl_data):
        dgl_feat, _ = torch.max(torch.stack([
            dgl.mean_nodes(dgl_data, 'h'),
            dgl.max_nodes(dgl_data, 'h'),
            dgl.mean_edges(dgl_data, 'h'),
            dgl.max_edges(dgl_data, 'h'),
        ], 2), -1)
        return dgl_feat


class GCN_readout_pool(torchnn.Module):
    def __init__(self, hidden_size):
        super().__init__()
        self.weight_node = torchnn.Linear(
            hidden_size*4, hidden_size, bias=True)
        self.activate = torchnn.LeakyReLU()

    def forward(self, dgl_data):
        dgl_feat, _ = torch.max(torch.stack([
            dgl.mean_nodes(dgl_data, 'h'),
            dgl.max_nodes(dgl_data, 'h'),
            dgl.mean_edges(dgl_data, 'h'),
            dgl.max_edges(dgl_data, 'h'),
        ], 2), -1)
        return dgl_feat


class Linear_process(torchnn.Module):
    def __init__(self, input_size, hidden_size, output_size, layer_num=0):
        super().__init__()
        self.layers = torchnn.ModuleList()

        self.layers.append(torchnn.Linear(
            input_size, hidden_size, bias=True))
        for _ in range(layer_num):
            self.layers.append(torchnn.Linear(
                hidden_size, hidden_size, bias=True))
        self.layers.append(torchnn.Linear(
            hidden_size, output_size, bias=True))
        # self.batchnorm = torch.nn.BatchNorm1d(hidden_size)
        self.activate = torchnn.LeakyReLU()

    def forward(self, data):
        for singlelayer in self.layers:
            data = singlelayer(data)
            # data = self.batchnorm(data)
        return data


class GCN_with_Topologi(torchnn.Module):
    def __init__(self, nodefeatsize, edgefeatsize, graphfeatsize, hidden_size, gcn_layers, output_size, activate):
        super().__init__()
        self.name = "gcnwithtopo"
        self.nodeedge_feat_init = DGLInit(
            nodefeatsize, edgefeatsize, hidden_size)
        self.base_feat_init = torchnn.Linear(
            graphfeatsize, hidden_size, bias=True)

        self.gcn_process = GCN_process(hidden_size, gcn_layers)
        self.gcn_predict = GCN_readout_cat(hidden_size)
        self.linear = Linear_process(
            hidden_size*2, hidden_size, output_size)
        self.predict_score = torch.nn.Linear(hidden_size*2, 1)
        self.final_activate = activate

    def forward(self, dgl_data, base_data):
        dgl_data_init = self.nodeedge_feat_init(dgl_data)
        dgl_data_gcn = self.gcn_process(dgl_data_init)
        dgl_data_feat = self.gcn_predict(dgl_data_gcn)

        base_feat = self.base_feat_init(base_data)
        class_predict = self.linear(torch.cat([dgl_data_feat, base_feat], -1))
        class_predict = self.final_activate(
            class_predict) if self.final_activate else class_predict
        score_predict = torch.nn.Sigmoid()(self.predict_score(
            torch.cat([dgl_data_feat, base_feat], -1)))
        return class_predict, score_predict


class OnlyBaseFeature(torchnn.Module):
    def __init__(self, nodefeatsize, edgefeatsize, graphfeatsize, hidden_size, gcn_layers, output_size, activate):
        super().__init__()
        self.name = "OnlyUseBaseFeature"
        self.nodeedge_feat_init = DGLInit(
            nodefeatsize, edgefeatsize, hidden_size)
        self.base_feat_init = torchnn.Linear(
            graphfeatsize, hidden_size, bias=True)

        self.gcn_predict = GCN_readout_cat(hidden_size)
        self.linear = Linear_process(
            hidden_size*2, hidden_size, output_size)
        self.predict_score = torch.nn.Linear(hidden_size*2, 1)
        self.final_activate = activate

    def forward(self, dgl_data, base_data):
        dgl_data_init = self.nodeedge_feat_init(dgl_data)
        dgl_data_feat = self.gcn_predict(dgl_data_init)

        base_feat = self.base_feat_init(base_data)
        class_predict = self.linear(torch.cat([dgl_data_feat, base_feat], -1))
        class_predict = self.final_activate(
            class_predict) if self.final_activate else class_predict
        score_predict = torch.nn.Sigmoid()(self.predict_score(
            torch.cat([dgl_data_feat, base_feat], -1)))
        return class_predict, score_predict


class OnlyDeepwalk(torchnn.Module):
    def __init__(self, nodefeatsize, edgefeatsize, graphfeatsize, hidden_size, gcn_layers, output_size, activate):
        super().__init__()
        self.name = "onlydeepwalk"

    def forward(self, dgl_data, base_data):
        mean_deepwalk_feat = dgl.mean_nodes(dgl_data, 'deepwalk')
        return mean_deepwalk_feat


if __name__ == '__main__':
    pass
