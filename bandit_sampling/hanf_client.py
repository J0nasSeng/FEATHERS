from collections import OrderedDict
import warnings

import flwr as fl
import torch
import torch.nn as nn
import numpy as np
from utils import get_dataset_loder
from model import Classifier
from trainer import DartsTrainer
from rtpt import RTPT
import config
from hyperparameters import Hyperparameters
from tensorboardX import SummaryWriter
from datetime import datetime as dt

warnings.filterwarnings("ignore", category=UserWarning)
DEVICE = torch.device("cuda:3" if torch.cuda.is_available() else "cpu")
EPOCHS = 1


def _test(net, testloader):
    """Validate the network on the entire test set."""
    criterion = torch.nn.CrossEntropyLoss()
    correct, total, loss = 0, 0, 0.0
    net.eval()
    with torch.no_grad():
        for feats, labels in testloader:
            #feats = feats.type(torch.FloatTensor)
            #labels = labels.type(torch.LongTensor)
            feats, labels = feats.to(DEVICE), labels.to(DEVICE)
            preds = net(feats)
            loss += criterion(preds, labels).item()
            _, predicted = torch.max(preds.data, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()
    accuracy = correct / total
    return loss, accuracy


# #############################################################################
# 2. Federation of the pipeline with Flower
# #############################################################################

def main(dataset, num_clients, classes=10, cell_nr=4, input_channels=1, out_channels=16, node_nr=7):
    """Create model, load data, define Flower client, start Flower client."""

    # Load model
    criterion = nn.CrossEntropyLoss()
    net = Classifier(classes, criterion, cell_nr, input_channels, out_channels, node_nr)
    net.to(DEVICE)

    # Load data
    fashion_mnist_iterator = get_dataset_loder(dataset, num_clients)
    train_data, test_data = next(fashion_mnist_iterator.get_client_data())
    date = dt.strftime(dt.now(), '%Y:%m:%d:%H:%M:%S')
    writer = SummaryWriter("./runs/Client_{}".format(date))
    darts_trainer = DartsTrainer(net, criterion, train_data, test_data, second_order_optim=True, 
                                device=DEVICE, batch_size=64, writer=writer)
    rtpt = RTPT('JS', 'HANF_Client', EPOCHS)
    rtpt.start()

    # Flower client
    class HANFClient(fl.client.NumPyClient):

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.epoch = 0
            self.hyperparameters = Hyperparameters.instance(config.HYPERPARAM_CONFIG_NR)
            self.epsilon = config.EPSILON
            self.epsilon_discount = config.EPSILON_DISCOUNT

        def get_parameters(self):
            return [val.cpu().numpy() for _, val in darts_trainer.model.state_dict().items()]

        def set_parameters_train(self, parameters, config):
            # obtain hyperparams and distribution
            hidx = int(parameters[-1][0])
            hyperparams = self.hyperparameters[hidx]
            darts_trainer.set_current_hyperparameter_config(hyperparams, hidx)
            
            # remove hyperparameter distribution from parameter list
            parameters = parameters[:-1]
            
            params_dict = zip(darts_trainer.model.state_dict().keys(), parameters)
            state_dict = OrderedDict({k: torch.tensor(v) for k, v in params_dict})
            darts_trainer.model.load_state_dict(state_dict, strict=True)

        def set_parameters_evaluate(self, parameters):
            params_dict = zip(darts_trainer.model.state_dict().keys(), parameters)
            state_dict = OrderedDict({k: torch.tensor(v) for k, v in params_dict})
            darts_trainer.model.load_state_dict(state_dict, strict=True)

        def fit(self, parameters, config):
            self.set_parameters_train(parameters, config)
            hconfig, hidx, before_loss, after_loss = darts_trainer.train_one_epoch(self.epoch)
            model_params = self.get_parameters()
            self.epoch += 1
            rtpt.step()
            return model_params, len(train_data), {'hidx': int(hidx), 'before': float(before_loss), 'after': float(after_loss)}

        def evaluate(self, parameters, config):
            self.set_parameters_evaluate(parameters)
            loss, accuracy = _test(darts_trainer.model, darts_trainer.valid_loader)
            return float(loss), len(test_data), {"accuracy": float(accuracy)}

        def get_hyperparams(self):
            writer.add_scalar('epsilon', self.epsilon, self.epoch)
            explore = np.random.choice([0, 1], p=[1 - self.epsilon, self.epsilon])
            if explore == 1:
                hidx = np.random.randint(0, len(self.hyperparameters))
                config = self.hyperparameters[hidx]
            else:
                hidx = np.argmax(self.reward_estimates)
                config = self.hyperparameters[hidx]
            self.epsilon = self.epsilon * self.epsilon_discount
            return hidx, config

    # Start client
    fl.client.start_numpy_client("[::]:{}".format(config.PORT), client=HANFClient())


if __name__ == "__main__":
    main(config.DATASET, config.CLIENT_NR, config.CLASSES, config.CELL_NR, 
        config.IN_CHANNELS, config.OUT_CHANNELS, config.NODE_NR)