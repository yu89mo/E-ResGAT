import argparse
import numpy as np
import time
import random
import torch
import torch.nn as nn
from torch.autograd import Variable
from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split
from loader import (load_sage, load_gat)
from models import (EGraphSage, EResGAT)

torch.set_default_tensor_type('torch.cuda.FloatTensor')

###CES-CIC -> CSE-CIC
###

np.random.seed(1)
random.seed(1)
data_class = {"UNSW-NB15":10,
              "Darknet":9,
              "CSE-CIC":7,
              "ToN-IoT":10}
data_lr = {"UNSW-NB15":0.007,
           "Darknet":0.003,
           "CSE-CIC":0.003,
           "ToN-IoT":0.01}
test_size = {"UNSW-NB15":210000,
             "Darknet":45000,
             "CSE-CIC":75000,
             "ToN-IoT":140000}


def fit(args):
    alg = args.alg
    data = args.dataset
    binary = args.binary
    residual = args.residual
    path = "datasets2/"+ data + "/"
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if alg == "sage":
        enc2, edge_feat, label, node_map, adj = load_sage(path, binary)
        model = EGraphSage(data_class[data], enc2, edge_feat, node_map, adj, residual)
    else:
        edge_feat, label, adj, adj_lists, config = load_gat(path, device, binary)
        model = EResGAT(
            num_of_layers=config['num_of_layers'],
            num_heads_per_layer=config['num_heads_per_layer'],
            num_features_per_layer=config['num_features_per_layer'],
            num_identity_feats=config['num_identity_feats'],
            edge_feat=edge_feat,
            adj=adj,
            adj_lists=adj_lists,
            device=device,
            add_skip_connection=config['add_skip_connection'],
            residual=residual,
            bias=config['bias'],
            dropout=config['dropout']
        ).to(device)

    # loss and optimizer
    loss_fn = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(filter(lambda p: p.requires_grad,
                                            model.parameters()),
                                 lr=data_lr[data])

    # train test split
    num_edges = len(edge_feat)
    label = label.to("cpu").numpy()
    train_val, test = train_test_split(np.arange(num_edges), test_size=test_size[data], stratify=label)
    train, val = train_test_split(train_val, test_size=5000, stratify=label[train_val])


    times = []
    trainscores = []
    valscores = []

    for epoch in range(2):
        print("Epoch: ", epoch)
        random.shuffle(train)
        epoch_start = time.time()
        for batch in range(int(len(train) / 1000)):  # batches in train data
            batch_edges = train[100 * batch:100 * (batch + 1)]  # 500 records per batch
            start_time = time.time()
            # training
            model.train()
            output, _ = model(batch_edges)
            if alg == "sage":
                train_output = output.data.numpy()
                acc_train = f1_score(label[batch_edges],
                                     train_output.argmax(axis=1),
                                     average="weighted")
                loss = model.loss(batch_edges,
                                       Variable(torch.LongTensor(label[np.array(batch_edges)])))
            else:
                _, out, _, idx = output
                train_output = out.index_select(0, idx)
                acc_train = f1_score(label[batch_edges],
                                     torch.argmax(train_output, dim=-1).to("cpu").numpy(),
                                     average="weighted")
                loss = loss_fn(train_output, torch.tensor(label[batch_edges]))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            end_time = time.time()
            times.append(end_time - start_time)
            trainscores.append(acc_train)

            print('batch: {:03d}'.format(batch + 1),
                  'loss_train: {:.4f}'.format(loss.item()),
                  'acc_train: {:.4f}'.format(acc_train.item()),
                  'time: {:.4f}s'.format(end_time - start_time))

            # if batch >= 179:
            #     break
        epoch_end = time.time()

        # Validation
        acc_val, loss_val, val_output = predict_(alg, model, label, loss_fn, val)
        valscores.append(acc_val)

        print('loss_val: {:.4f}'.format(loss_val),
              'acc_val: {:.4f}'.format(acc_val.item()),
              'average batch time: {:.4f}s'.format(np.mean(times)),
              'epoch time: {:.2f}min'.format((epoch_end - epoch_start)/60.0))

    # Testing
    acc_test, loss_test, predict_output = predict_(alg, model, label, loss_fn, test)
    print("Test set results:", "loss= {:.4f}".format(loss_test),
          "accuracy= {:.4f}".format(acc_test.item()),
          "label acc=", f1_score(label[test], predict_output, average=None))


def predict_(alg, model, label, loss_fn, data_idx):
    predict_output = []
    loss = 0.0
    # emb = []
    for batch in range(int(len(data_idx) / 500)):
        batch_edges = data_idx[500 * batch:500 * (batch + 1)]
        batch_output, _ = model(batch_edges)
        if alg == "sage":
            batch_output = batch_output.data.numpy().argmax(axis=1)
            batch_loss = model.loss(batch_edges,
                              Variable(torch.LongTensor(label[np.array(batch_edges)])))
        else:
            _, out, _, idx = batch_output
            batch_output = out.index_select(0, idx)
            
            # batch_output = (batch_output >= 1).int()*-1
            batch_loss = loss_fn(batch_output, torch.tensor(label[batch_edges]))
            batch_output = torch.argmax(batch_output, dim=-1).to("cpu").numpy()
        predict_output.extend(batch_output)
        loss += batch_loss.item()
        # emb.append(embed)
    loss /= batch + 1
    acc = f1_score(label[data_idx], predict_output, average="weighted")
    # emb = torch.stack(emb).view(5000, -1)
    return acc, loss, predict_output


# -----------------------------------------------------------------------------

if __name__ == '__main__':
    ALG = ['sage', 'gat']
    DATA = ['UNSW-NB15', 'Darknet', 'CSE-CIC', 'ToN-IoT']

    p = argparse.ArgumentParser()
    p.add_argument('--alg',
                   help='algorithm to use.',
                   default='gat',
                   choices=ALG)
    p.add_argument('--dataset',
                   help='Experimental dataset.',
                   type=str,
                   default='Darknet',
                   choices=DATA)
    p.add_argument('--binary',
                   help='Perform binary or muticlass task',
                   type=bool,
                   default=True)
    p.add_argument('--residual',
                   help='Apply modified model with residuals or not',
                   type=bool,
                   default=True)
    # Parse and validate script arguments.
    args = p.parse_args()

    # Training and testing
    fit(args)
