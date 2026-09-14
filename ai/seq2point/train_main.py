import argparse
import os
from remove_space import remove_space
from seq2point_train import Trainer
# Allows a model to be trained from the terminal.

training_directory="~/mingjun/research/housedata/refit/kettle/kettle_training_.csv"
validation_directory="~/mingjun/research/housedata/refit/kettle/kettle_validation_.csv"

parser = argparse.ArgumentParser(description="Train sequence-to-point learning for energy disaggregation. ")

parser.add_argument("--appliance_name", type=remove_space, default="kettle", help="The name of the appliance to train the network with. Default is kettle. Available are: kettle, fridge, washing machine, dishwasher, and microwave. ")
parser.add_argument("--batch_size", type=int, default="1000", help="The batch size to use when training the network. Default is 1000. ")
parser.add_argument("--crop", type=int, default="1000", help="The number of rows of the dataset to take training data from. Default is 10000. ")
#parser.add_argument("--pruning_algorithm", type=remove_space, default="default", help="The pruning algorithm that the network will train with. Default is none. Available are: spp, entropic, threshold. ")
parser.add_argument("--network_type", type=remove_space, default="seq2point", help="The seq2point architecture to use. ")
parser.add_argument("--epochs", type=int, default="2", help="Number of epochs. Default is 10. ")
parser.add_argument("--input_window_length", type=int, default="599", help="Number of input data points to network. Default is 599.")
parser.add_argument("--validation_frequency", type=int, default="1", help="How often to validate model. Default is 1. ")
parser.add_argument("--training_directory", type=str, default=training_directory, help="The dir for training data. ")
parser.add_argument("--validation_directory", type=str, default=validation_directory, help="The dir for validation data. ")
parser.add_argument("--skip_rows_train", type=int, default="10000000", help="The number of rows at the start of the training data to skip. Default is 10000000; use 0 for smaller datasets such as UK-DALE. ")
parser.add_argument("--validation_steps", type=int, default="100", help="The number of validation batches per validation run. Default is 100; use 0 to validate on the whole validation set. ")
parser.add_argument("--saved_models_dir", type=str, default="saved_models/", help="The directory to save the trained model in. Default is saved_models/. ")

arguments = parser.parse_args()

# Need to provide the trained model
os.makedirs(arguments.saved_models_dir, exist_ok=True)
save_model_dir = os.path.join(arguments.saved_models_dir, arguments.appliance_name + "_" + arguments.network_type + "_model.h5")

trainer = Trainer(arguments.appliance_name, arguments.batch_size, arguments.crop, arguments.network_type,
                  arguments.training_directory, arguments.validation_directory,
                  save_model_dir,
                  epochs = arguments.epochs, input_window_length = arguments.input_window_length,
                  validation_frequency = arguments.validation_frequency,
                  skip_rows_train = arguments.skip_rows_train,
                  validation_steps = arguments.validation_steps)
trainer.train_model()

