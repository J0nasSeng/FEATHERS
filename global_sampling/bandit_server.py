import flwr as fl
from hanf_bandit_strategy import HANFStrategy
import torch.nn as nn
from model import Classifier
import config

def start_server(beta, epsilon, eta, log_dir, rounds, reinit, 
                num_clients, num_fit_clients, num_val_clients):
    criterion = nn.CrossEntropyLoss()
    net = Classifier(config.CLASSES, criterion, config.CELL_NR, 
                    config.IN_CHANNELS, config.OUT_CHANNELS, config.NODE_NR)

    # Define strategy
    strategy = HANFStrategy(
        fraction_fit=0.5,
        fraction_eval=0.5,
        initial_net=net,
    )

    # Start server
    fl.server.start_server(
        server_address="[::]:8086",
        config={"num_rounds": rounds},
        strategy=strategy,
    )

if __name__ == "__main__":
    start_server(config.BETA, config.EPSILON, config.ETA, 
                config.LOG_DIR, config.ROUNDS, config.REINIT, config.CLIENT_NR,
                config.MIN_TRAIN_CLIENTS, config.MIN_VAL_CLIENTS)
