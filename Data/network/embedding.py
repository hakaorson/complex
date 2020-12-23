import math
import sys
import numpy as np
import re
import networkx as nx
import os
import time
import queue
import random
from goatools import obo_parser
import pandas as pd
import subprocess
from embedding_support.gae import train as gaetrain
from matplotlib import pyplot as plt
from sklearn.preprocessing import StandardScaler


def findSubcellWords(str_input):
    str_remove_head = re.sub('SUBCELLULAR LOCATION: ', "", str_input)
    str_remove_note = re.sub('Note=.*', "", str_remove_head)
    str_remove_bracket = re.sub('{([^{}]*?)}', "", str_remove_note)

    str_splited = re.split('\.|;|,', str_remove_bracket)
    result = []
    for single_str in str_splited:
        single_str = single_str.strip().capitalize()
        if single_str:
            result.append(single_str)
    # print(result)
    return result


def save(datas, path):
    with open(path, 'w') as f:
        for data in datas:
            f.write(data+'\n')


# 读取edge
def read_edges(edges_path):
    nodes, edges = set(), list()
    with open(edges_path) as f:
        for line in f:
            if '\t' in line:
                linelist = line.strip().split('\t')
            else:
                linelist = line.strip().split(' ')
            edges.append(linelist)
            for singleid in linelist[:2]:
                nodes.add(singleid)
    return list(nodes), edges


# 读取graph
def construct_graph(graph_path, direction=True):
    _, edges = read_edges(graph_path)
    if direction:
        res = nx.DiGraph()
    else:
        res = nx.Graph()
    for edge in edges:
        if len(edge) == 3:
            res.add_edge(edge[0], edge[1], weight=edge[2])
        else:
            res.add_edge(edge[0], edge[1])
    return res


# 读取mapping
def read_mapping(mapping_path):
    res = {}
    with open(mapping_path) as f:
        for line in f:
            linelist = list(line.strip().split('\t'))
            res[linelist[0]] = linelist[1:]
    return res


def read_uniprotkb(path):
    res = {}
    with open(path, 'r')as f:
        heads = next(f)
        headslist = heads.strip().split('\t')
        # 使用 yourlist作为id，如果实在没有，可以使用entry作为id
        enterIndex = -1
        for index, name in enumerate(headslist):
            if name[:8] == "yourlist":
                enterIndex = index
        enterIndex = enterIndex if enterIndex != -1 \
            else headslist.index('Entry')
        seqIndex = headslist.index('Sequence')
        subcellIndex = headslist.index('Subcellular location [CC]')
        # print(subcellIndex)
        goIndex = headslist.index('Gene ontology IDs')
        domainIndex = headslist.index('Cross-reference (Pfam)')
        for line in f:
            linelist = line.strip().split('\t')
            data = {}
            data['seq'] = linelist[seqIndex] if linelist[seqIndex] != '' else []
            data['go'] = linelist[goIndex].replace(' ', '').split(
                ';') if linelist[goIndex] != '' else []
            data['subcell'] = findSubcellWords(
                linelist[subcellIndex]) if linelist[subcellIndex] != '' else []
            linelist[domainIndex] = linelist[domainIndex].strip()
            data['domain'] = linelist[domainIndex][:-
                                                   1].split(';') if linelist[domainIndex] != '' else []
            if len(linelist[enterIndex]) > 7:
                print()
            res[linelist[enterIndex]] = data
    return res


# 寻找某些节点的某些跳邻居
def findNeisInGraph(graph, nodes, skip):
    nodes_valid = []
    for node in nodes:
        if node in graph.nodes:
            nodes_valid.append(node)
    if len(nodes_valid) == 0:
        return []

    res = nodes_valid
    visited = set(nodes_valid)
    que = queue.Queue()
    for node in nodes_valid:
        que.put(node)
    prenode = res[-1]
    while skip and not que.empty():
        cur_node = que.get()
        cur_neibors = list(graph.neighbors(cur_node))
        for nei in cur_neibors:
            if nei not in visited:
                que.put(nei)
                visited.add(nei)
                res.append(nei)
        if cur_node == prenode:
            prenode = res[-1]
            skip -= 1
    return res


# 计算域相互作用
def compute_edge_feat_domain(graph, v0_domains, v1_domains):
    res = []
    v0_neis_1 = findNeisInGraph(graph, v0_domains, 1)
    v1_neis_1 = findNeisInGraph(graph, v1_domains, 1)

    def find_links(v0_targets, v1_targets):
        domain_weight = 0
        direct_linknum = 0
        for v0 in v0_targets:
            for v1 in v1_targets:
                if graph.has_node(v0) and graph.has_node(v1) and graph.has_edge(v0, v1):
                    domain_weight += int(graph[v0][v1]['weight'])
                    direct_linknum += 1
        return [domain_weight, direct_linknum]

    res.append(len(set(v0_domains) & set(v1_domains)))  # 两个域有几个相同地
    res.append(len(set(v0_domains) | set(v1_domains)))
    res.append(len(set(v0_domains) - set(v1_domains)))
    res.append(len(set(v1_domains) - set(v0_domains)))
    res.extend(find_links(v0_domains, v1_domains))
    res.extend(find_links(v0_neis_1, v1_neis_1))
    res.append(len(set(v0_neis_1) & set(v1_neis_1)))  # 一阶共同邻居
    return res


def compute_edge_feat_subcell(graph, v0_subcells, v1_subcells):  # 计算亚细胞作用
    res = []
    and_num = len(set(v0_subcells) & set(v1_subcells))
    or_num = len(set(v0_subcells) | set(v1_subcells))
    v0_neis_1 = findNeisInGraph(graph, v0_subcells, 2)
    v1_neis_1 = findNeisInGraph(graph, v1_subcells, 2)
    res.append(and_num)
    res.append(or_num)
    res.append(and_num/or_num if or_num else 0)
    res.append(len(set(v0_neis_1) & set(v1_neis_1)))
    # print(res)
    return res


class go_compute():
    def __init__(self, obopath, allproteingopath):
        self.graph = obo_parser.GODag(
            obopath, optional_attrs="relationship", prt=None)
        self.computed_SV = {}
        self.lin_static, self.allproteinnum = self.static_allgo_info(
            allproteingopath)

    def findDirectParent(self, go_term):
        is_a_parents = list(go_term.parents)
        part_of_parents = list(
            go_term.relationship['part_of']) if 'part_of' in go_term.relationship.keys() else []
        return is_a_parents, part_of_parents

    def extract_graph(self, go_term):
        '''
        从go注释网络里面提取局部网络
        '''
        nodes = set([go_term.id])
        edges = dict()
        visited = set()
        que = queue.Queue()
        que.put(go_term)
        while not que.empty():
            cur_node = que.get()
            if cur_node in visited:
                continue
            visited.add(cur_node)
            is_a_parents, part_of_parents = self.findDirectParent(cur_node)
            for part_of_p in part_of_parents:
                que.put(part_of_p)
                edges[(cur_node.id, part_of_p.id)] = 0.6
                nodes.add(part_of_p.id)
            for is_a_p in is_a_parents:
                que.put(is_a_p)
                edges[(cur_node.id, is_a_p.id)] = 0.8
                nodes.add(is_a_p.id)
        nodemap = {}
        for index, node in enumerate(nodes):
            nodemap[node] = index
        matrix = [[0 for j in range(len(nodemap))]
                  for i in range(len(nodemap))]
        for edge in edges.keys():
            v0, v1 = nodemap[edge[0]], nodemap[edge[1]]
            matrix[v0][v1] = edges[edge]
        res_nodes = list(nodes)
        res_edges = [[edge[0], edge[1], edges[edge]] for edge in edges]
        return res_nodes, res_edges

    def compute_sv(self, go):
        if go in self.computed_SV.keys():
            return self.computed_SV[go]
        if go not in self.graph.keys():
            return {}
        begin = self.graph.query_term(go)
        nodes, edges = self.extract_graph(begin)
        # 为拓扑排序汇集所有的边信息和节点信息
        edge_in_num = {}
        edges_sum = {}
        node_res = {}
        for node in nodes:
            node_res[node] = 0.0
            edge_in_num[node] = 0
        node_res[begin.id] = 1.0
        for v0, v1, w in edges:
            edge_in_num[v1] += 1
            if v0 in edges_sum.keys():
                edges_sum[v0].append([v1, w])
            else:
                edges_sum[v0] = [[v1, w]]
        que = queue.Queue()
        que.put(begin.id)
        # 执行拓扑排序算法
        while not que.empty():
            cur = que.get()
            if cur in edges_sum.keys():
                for parent, w in edges_sum[cur]:
                    edge_in_num[parent] -= 1
                    node_res[parent] = max(node_res[parent], node_res[cur]*w)
                    if edge_in_num[parent] == 0:
                        que.put(parent)
        self.computed_SV[go] = node_res
        return node_res

    def computeWangSimSingle(self, v0_go, v1_go):
        v0_SV = self.compute_sv(v0_go)
        v1_SV = self.compute_sv(v1_go)
        sum_v0, sum_v1, sum_com = 0, 0, 0
        commons = v0_SV.keys() & v1_SV.keys()
        if len(commons) == 0:
            return 0
        for com in commons:
            sum_com += v0_SV[com]
            sum_com += v1_SV[com]
        for v0 in v0_SV.keys():
            sum_v0 += v0_SV[v0]
        for v1 in v1_SV.keys():
            sum_v1 += v1_SV[v1]
        return sum_com/(sum_v0+sum_v1)

    def computeWangSim(self, v0_gos, v1_gos):
        matrix = [[0 for j in range(len(v1_gos)+1)]
                  for i in range(len(v0_gos)+1)]
        for v0_index, v0_go in enumerate(v0_gos):
            for v1_index, v1_go in enumerate(v1_gos):
                matrix[v0_index][v1_index] = self.computeWangSimSingle(
                    v0_go, v1_go)
                matrix[v0_index][-1] = max(matrix[v0_index]
                                           [-1], matrix[v0_index][v1_index])
                matrix[-1][v1_index] = max(matrix[-1]
                                           [v1_index], matrix[v0_index][v1_index])
        temp_sum = 0
        for i in range(len(v0_gos)):
            temp_sum += matrix[i][-1]
        for j in range(len(v1_gos)):
            temp_sum += matrix[-1][j]
        res = temp_sum/(len(v0_gos)+len(v1_gos)
                        ) if len(v0_gos) or len(v1_gos) else 0
        return res

    def static_allgo_info(self, allgopath):
        static = {}
        proteinnum = 0
        with open(allgopath, 'r')as f:
            next(f)
            for line in f:
                proteinnum += 1
                linedatas = line.strip().split('\t')
                gos = list(linedatas[1].split(';')) if len(
                    linedatas) > 1 else []
                for go in gos:
                    static[go] = static.get(go, 0)+1
        return static, proteinnum

    def computeLinSim(self, v0_gos, v1_gos):
        v0_parents, v1_parents = set(), set()
        for v0_go in v0_gos:
            v0_query = self.graph.query_term(v0_go)
            if v0_query:
                v0_parents = v0_parents | set(v0_query.parents)
        for v1_go in v1_gos:
            v1_query = self.graph.query_term(v1_go)
            if v1_query:
                v1_parents = v1_parents | set(v1_query.parents)
        common_parents = v0_parents & v1_parents
        common_parents = [item.id for item in common_parents]

        allkeys = self.lin_static.keys()
        min_common = sys.maxsize
        max_v0, max_v1 = 0, 0
        for cpa in common_parents:
            min_common = min(
                min_common, self.lin_static[cpa] if cpa in allkeys else sys.maxsize)
        for v0_go in v0_gos:
            max_v0 = max(
                max_v0, self.lin_static[v0_go] if v0_go in allkeys else 0)
        for v1_go in v1_gos:
            max_v1 = max(
                max_v1, self.lin_static[v1_go] if v1_go in allkeys else 0)
        if max_v0 == 0 or max_v1 == 0 or min_common == sys.maxsize:
            return 0
        return 2*math.log(min_common/self.allproteinnum)/(math.log(max_v0/self.allproteinnum)+math.log(max_v1/self.allproteinnum))

    def compute_edge_feat_go(self, v0_gos, v1_gos):
        '''
        计算go相似性
        '''
        res = []
        '''
        wang相似性，取自徐斌师兄的论文
        两个蛋白质拆尊尊自己的go注释
        计算其中任意两个之间的go相似性

        使用go图可以提取go的关系，其中parent是直接关系（表示is_a），而在relationship中的part_of关键字表示的是part_of的关系
        具体的计算过程可以从论文中得出
        '''
        res.append(self.computeWangSim(v0_gos, v1_gos))
        '''
        lin相似性，取自徐斌论文第五章
        '''
        res.append(self.computeLinSim(v0_gos, v1_gos))
        '''
        论文Predicting protein complex in protein interaction network - a supervised learning based method提供了一种go特征的计算方法
        '''
        '''
        其他特征
        '''
        common_go_nums = len(set(v0_gos) & set(v1_gos))
        all_go_nums = len(set(v0_gos) | set(v1_gos))
        res.append(common_go_nums)
        res.append(all_go_nums)
        res.append(common_go_nums/all_go_nums if all_go_nums else 0)
        return res


def compute_edge_feats(name, edges, nodedatas):
    domain_net = construct_graph(
        "embedding_support/domain/domain_graph", direction=False)
    subcell_map = construct_graph(
        "embedding_support/subcell/subcell_graph", direction=True)
    go_computor = go_compute(
        "embedding_support/go/origin_data/go-basic.obo", "embedding_support/go/origin_data/uniprot-filtered-reviewed_yes.tab")
    protein_graph = construct_graph(name+'/edges')
    res = []
    notmatched_data = {'domain': [], 'subcell': [], 'go': [], 'seq': ""}
    for edge in edges:
        v0, v1 = edge
        v0_data = nodedatas[v0] if v0 in nodedatas.keys() else notmatched_data
        v1_data = nodedatas[v1] if v1 in nodedatas.keys() else notmatched_data
        tempEmb = {}
        tempEmb['id'] = [" ".join(edge)]
        tempEmb['domain'] = compute_edge_feat_domain(
            domain_net, v0_data['domain'], v1_data['domain'])
        tempEmb['subcell'] = compute_edge_feat_subcell(
            subcell_map, v0_data['subcell'], v1_data['subcell'])
        tempEmb['go'] = go_computor.compute_edge_feat_go(
            v0_data['go'], v1_data['go'])
        tempEmb['neibor'] = [
            len(set(protein_graph.neighbors(v0)) & set(protein_graph.neighbors(v1)))]
        res.append(tempEmb)
    return res


# 计算blast
def compute_node_feat_blast(mapping, node):
    # 不明意义的420维度数据集
    if node in mapping.keys():
        return list(map(float, mapping[node]))
    else:
        return 420*[0.0]


def deepwalk(name, nodes, edges):
    node_map = {}
    for index, node in enumerate(nodes):
        node_map[node] = index
    edges_path = "embedding_support/deepwalk/{}_edges".format(name)
    embed_path = "embedding_support/deepwalk/{}_embed".format(name)
    if os.path.exists(embed_path):
        os.remove(embed_path)
    with open(edges_path, 'w') as f:
        for v0, v1 in edges:
            string = "{} {}\n".format(node_map[v0], node_map[v1])
            f.write(string)
    cmd = ["deepwalk", "--input", os.path.abspath(
        edges_path), "--output", os.path.abspath(embed_path), "--format", "edgelist"]
    subprocess.Popen(cmd)  # TODO 暂时不需要执行
    while True:
        if not os.path.exists(embed_path):
            time.sleep(1)
        else:
            time.sleep(3)
            break
    with open(embed_path, 'r')as f:
        next(f)
        res = {}
        for line in f:
            line_list = list(line.strip().split(" "))
            nodeid = nodes[int(line_list[0])]
            res[nodeid] = line_list[1:]
        return res


def compute_node_feats(name, nodes, edges, uniprot_data):
    blast_map = read_mapping("embedding_support/blast/POSSUM_DATA")
    deepwalkres = deepwalk(name, nodes, edges)
    protein_default_size = sum([len(uniprot_data[key]['seq'])
                                for key in uniprot_data.keys()])/len(uniprot_data.keys())
    res = []
    for node in nodes:
        tempEmb = {}
        tempEmb['id'] = [node]
        # tempEmb['blast'] = compute_node_feat_blast(blast_map, node)#不再计算blast特征
        tempEmb['deepwalk'] = deepwalkres[node]
        tempEmb['len'] = [len(uniprot_data[node]['seq']) if node in uniprot_data.keys(
        ) else int(protein_default_size)]
        res.append(tempEmb)
    return res


def writebackdictfeat(datas, path):
    with open(path, 'w') as f:
        example_data = datas[0]
        names = []
        for name in example_data.keys():
            names.extend(
                [name+'_'+str(index) for index in range(len(example_data[name]))])
        f.write('\t'.join(names)+'\n')
        for data in datas:
            items = []
            for key in data.keys():
                items.extend(data[key])
            items = list(map(str, items))
            strings = '\t'.join(items)+'\n'
            f.write(strings)


def get_datas(data_path):
    datas = []
    with open(data_path, 'r') as f:
        names = list(next(f).strip().split('\t'))
        for item in f:
            item_splited = item.strip().split('\t')
            datas.append([item_splited[0]]+list(map(float, item_splited[1:])))
    return names, datas


def processFeat(path, typ):
    names, datas = get_datas(path)
    df = pd.DataFrame(datas, columns=names)
    if typ == "node":
        df["len_0_log"] = df["len_0"].apply(np.log)
        df["len_0_sqrt"] = df["len_0"].apply(np.sqrt)
        df.drop(['len_0'], axis=1, inplace=True)
    else:
        df["domain_4"] = df["domain_4"].apply(np.log1p)
        df["domain_5"] = df["domain_5"].apply(np.log1p)
        df["domain_6"] = df["domain_6"].apply(np.log1p)
        df["domain_7"] = df["domain_7"].apply(np.log1p)
        df["domain_8"] = df["domain_8"].apply(np.log1p)
        df["go_3"] = df["go_3"].apply(np.log1p)
    new_names, new_datas = [], []
    for name in df:
        new_names.append(name)
        new_datas.append(df[name].tolist())
    with open(path+"_processed", 'w')as f:
        f.write('\t'.join(new_names)+'\n')
        for index in range(len(new_datas[0])):
            the_id = new_datas[0][index]
            datas = [new_datas[col][index]
                     for col in range(1, len(new_names))]
            new_line = [the_id]+['%.3f' % f for f in datas]
            f.write('\t'.join(new_line)+'\n')


def main(name):
    nodes, edges = read_edges(name+'/edges')
    # 需要在uniprot下载数据，放置到对应的网络数据下
    # 'Entry','Sequence','Subcellular location [CC]','Gene ontology IDs','Cross-reference (Pfam)'
    uniprotkb_datas = read_uniprotkb(name+'/origin_data/uniprot_data')
    node_feats = compute_node_feats(name, nodes, edges, uniprotkb_datas)
    edge_feats = compute_edge_feats(name, edges, uniprotkb_datas)
    writebackdictfeat(node_feats, name+'/nodes_feat')
    writebackdictfeat(edge_feats, name+'/edges_feat')
    processFeat(name+'/nodes_feat', 'node')
    processFeat(name+'/edges_feat', 'edge')


def read_feat_datas(data_path):
    ids, datas = [], []
    with open(data_path, 'r') as f:
        featsnames = next(f).strip().split('\t')
        for item in f:
            item_splited = item.strip().split('\t')
            ids.append(item_splited[0])
            datas.append(list(map(float, item_splited[1:])))
    return ids, datas, featsnames


def append_gae_feat(name):
    nodes, node_feats, nfeat_names = read_feat_datas(
        name+'/nodes_feat_processed')
    edges, edge_feats, efeat_names = read_feat_datas(
        name+'/edges_feat_processed')
    st_process = StandardScaler()
    node_feats = st_process.fit_transform(np.array(node_feats))
    edge_feats = st_process.fit_transform(np.array(edge_feats))
    biggraph = nx.Graph()
    for node_index in range(len(nodes)):
        biggraph.add_node(nodes[node_index], w=node_feats[node_index])
    for edge_index in range(len(edges)):
        v0, v1 = list(edges[edge_index].split(' '))
        biggraph.add_edge(v0, v1, w=edge_feats[edge_index])
    for node in biggraph.nodes:
        neibor_feats = []
        for nei in nx.neighbors(biggraph, node):
            neibor_feats.append(biggraph[node][nei]['w'])
        mean_feat = list(np.mean(np.array(neibor_feats), 0))
        fusion_feat = mean_feat+list(biggraph.nodes[node]['w'])
        biggraph.nodes[node]['f'] = fusion_feat
    gaeres = gaetrain.gae_embedding(biggraph)
    node_feats = np.concatenate((node_feats, gaeres), -1)
    nfeat_names.extend('gae_{}'.format(index)
                       for index in range(gaeres.shape[-1]))
    write_back(name+'/nodes_feat_final', nodes, node_feats, nfeat_names)
    write_back(name+'/edges_feat_final', edges, edge_feats, efeat_names)


def write_back(path, ids, feats, names):
    with open(path, 'w') as f:
        name_string = '\t'.join(names)+'\n'
        f.write(name_string)
        for index in range(len(ids)):
            single_id = [ids[index]]
            single_feat = list(feats[index])
            single_line = '\t'.join(map(str, single_id+single_feat))+'\n'
            f.write(single_line)


def showPPIgraph(path):
    graphpath, savepath = path+"/edges", path+"/pictures"
    graph = construct_graph(graphpath, direction=False)
    # pos = nx.spring_layout(graph)
    de = [graph.degree[node] for node in graph.nodes]
    pos = nx.spring_layout(graph)
    nx.draw_networkx(graph, pos=pos, node_size=de, with_labels=False,
                     node_color=de, linewidths=None, width=1.0, edge_color='#858585')
    plt.savefig(savepath)
    plt.show()
    pass


def statictic_bigraph(path):
    graphpath = path+"/edges"
    ppigraph = construct_graph(graphpath, direction=False)
    info = {}
    info['node_num'] = len(ppigraph.nodes)
    info['edge_num'] = len(ppigraph.edges)
    # info['clustering'] = nx.clustering(ppigraph)
    info['average_degree'] = (info['edge_num']*2)/info['node_num']
    info['density'] = nx.density(ppigraph)
    info['average_clustering'] = nx.average_clustering(ppigraph)
    info['number_connected_components'] = nx.number_connected_components(
        ppigraph)
    maxsub_nodes = next(nx.connected_components(ppigraph))
    maxsub_ppigraph = nx.subgraph(ppigraph, maxsub_nodes)
    info['max_subgraph_node_num'] = len(maxsub_ppigraph.nodes)
    # info['betweenness_centrality'] = nx.betweenness_centrality(maxsub_ppigraph)
    info['transitivity'] = nx.transitivity(ppigraph)
    info['diameter'] = nx.diameter(maxsub_ppigraph)
    print(path)
    print(info)


if __name__ == "__main__":
    # for name in ['DIP', 'Krogan', 'Biogrid', 'Gavin']:
    #     statictic_bigraph(name)

    # main("Biogrid")
    # append_gae_feat("Biogrid")

    main("DIP")
    append_gae_feat("DIP")
