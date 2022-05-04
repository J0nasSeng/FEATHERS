from collections import OrderedDict
from turtle import rt
import warnings

import flwr as fl
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torch.autograd import Variable
import numpy as np
from utils import get_dataset_loder
from rtpt import RTPT
import config
from hyperparameters import Hyperparameters
from tensorboardX import SummaryWriter
from datetime import datetime as dt
import argparse
from model import NetworkCIFAR, NetworkImageNet
from genotypes import GENOTYPE

warnings.filterwarnings("ignore", category=UserWarning)
EPOCHS = 1


def _test(net, testloader, device):
    """Validate the network on the entire test set."""
    criterion = torch.nn.CrossEntropyLoss()
    correct, total, loss = 0, 0, 0.0
    net.eval()
    with torch.no_grad():
        for feats, labels in testloader:
            #feats = feats.type(torch.FloatTensor)
            #labels = labels.type(torch.LongTensor)
            feats, labels = feats.to(device), labels.to(device)
            preds = net(feats)
            loss += criterion(preds, labels).item()
            _, predicted = torch.max(preds.data, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()
    accuracy = correct / total
    return loss, accuracy

def train(train_queue, model, criterion, optimizer, device):

  for step, (input, target) in enumerate(train_queue):
    model.train()

    input = input.to(device, non_blocking=True)
    target = target.to(device, non_blocking=True)

    optimizer.zero_grad()
    logits, _ = model(input)
    loss = criterion(logits, target)

    loss.backward()
    nn.utils.clip_grad_norm(model.parameters(), 5.)
    optimizer.step()

    if step % 50 == 0:
        print("Step %03d" % step)
  
  return model

# #############################################################################
# 2. Federation of the pipeline with Flower
# #############################################################################

def main(dataset, num_clients, device, classes=10, cell_nr=4, input_channels=1, out_channels=16, node_nr=7):
    """Create model, load data, define Flower client, start Flower client."""

    # Load data
    fashion_mnist_iterator = get_dataset_loder(dataset, num_clients)
    train_data, test_data = next(fashion_mnist_iterator.get_client_data())
    date = dt.strftime(dt.now(), '%Y:%m:%d:%H:%M:%S')
    writer = SummaryWriter("./runs/Client_val_{}".format(date))
    rtpt = RTPT('JS', 'HANF_Client', EPOCHS)
    rtpt.start()

    # Flower client
    class HANFClient(fl.client.NumPyClient):

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.epoch = 0
            self.hyperparameters = Hyperparameters.instance(config.HYPERPARAM_CONFIG_NR)
            self.criterion = torch.nn.CrossEntropyLoss()
            self.criterion = self.criterion.to(device)
            if config.DATASET == 'cifar10':
                self.model = NetworkCIFAR(out_channels, classes, cell_nr, False, genotype=GENOTYPE)
            elif config.DATASET == 'imagenet':
                self.model = NetworkImageNet(out_channels, classes, cell_nr, False, genotype=GENOTYPE)
            self.model = self.model.to(device)
            self.optimizer = None
            self.train_loader = DataLoader(train_data, 64, pin_memory=True, num_workers=2)
            self.val_loader = DataLoader(test_data, 64, pin_memory=True, num_workers=2)

        def get_parameters(self):
            return [val.cpu().numpy() for _, val in self.model.state_dict().items()]

        def set_parameters_train(self, parameters, config):
            # obtain hyperparams and distribution
            hidx = int(parameters[-1][0])
            hyperparams = self.hyperparameters[hidx]
            self.set_current_hyperparameter_config(hyperparams, hidx)
            
            # remove hyperparameter distribution from parameter list
            parameters = parameters[:-1]
            
            params_dict = zip(self.model.state_dict().keys(), parameters)
            state_dict = OrderedDict({k: torch.tensor(v) for k, v in params_dict})
            self.model.load_state_dict(state_dict, strict=True)

        def set_parameters_evaluate(self, parameters):
            params_dict = zip(self.model.state_dict().keys(), parameters)
            state_dict = OrderedDict({k: torch.tensor(v) for k, v in params_dict})
            self.model.load_state_dict(state_dict, strict=True)

        def fit(self, parameters, cfg):
            self.set_parameters_train(parameters, cfg)
            before_loss, _ = _test(self.model, self.val_loader, device)
            for e in range(EPOCHS):
                rtpt.step()
                self.epoch += 1
                self.model.drop_path_prob = config.DROP_PATH_PROB * self.epoch / config.ROUNDS
                self.model = train(self.train_loader, self.model, self.criterion, self.optimizer, device)
            after_loss, _ = _test(self.model, self.val_loader, device)
            model_params = self.get_parameters()
            return model_params, len(train_data), {'hidx': int(self.hidx), 'before': float(before_loss), 'after': float(after_loss)}

        def evaluate(self, parameters, config):
            self.set_parameters_evaluate(parameters)
            loss, accuracy = _test(self.model, self.val_loader, device)
            return float(loss), len(test_data), {"accuracy": float(accuracy)}

        def set_current_hyperparameter_config(self, hyperparam, idx):
            self.hyperparam_config = hyperparam
            self.hidx = idx
            if self.optimizer is None:
                self.optimizer = torch.optim.SGD(self.model.parameters(), self.hyperparam_config['learning_rate'], 0.9, weight_decay=3e-4)
            else:
                for g in self.optimizer.param_groups:
                    g['lr'] = self.hyperparam_config['learning_rate']
            
    # Start client
    fl.client.start_numpy_client("[::]:{}".format(config.PORT), client=HANFClient())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--gpu', default='0', type=str)

    args = parser.parse_args()
    device = torch.device('cuda:{}'.format(args.gpu))
    main(config.DATASET, config.CLIENT_NR, device, config.CLASSES, config.CELL_NR, 
        config.IN_CHANNELS, config.OUT_CHANNELS, config.NODE_NR)