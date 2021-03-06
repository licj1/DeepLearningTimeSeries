import torch
import torch.nn as nn
from torch.autograd import Variable
from torch.optim.lr_scheduler import *
import torch.optim as optim
import torch.nn.functional as F

import math
import numpy as np

from MyPackage import Trainer

SEED = 1337


class Encoder(nn.Module):
    def __init__(self,
                 input_size,
                 num_layers=2,
                 hidden_size=10,
                 cell_type='LSTM'):
        super(Encoder, self).__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.cell_type = cell_type

        assert cell_type in ['LSTM', 'RNN', 'GRU'], 'RNN type is not supported'

        if cell_type == 'LSTM':
            self.encoder_cell = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        if cell_type == 'GRU':
            self.encoder_cell = nn.GRU(input_size, hidden_size, num_layers, batch_first=True)
        if cell_type == 'RNN':
            self.encoder_cell = nn.RNN(input_size, hidden_size, num_layers, batch_first=True)

    def forward(self, x, hidden=None):
        # returns output variable - all hidden states for seq_len, hindden state - last hidden state
        output, hidden_state = self.encoder_cell(x, hidden)

        return output, hidden_state


class Attn(nn.Module):
    def __init__(self,
                 method,
                 hidden_size):

        super(Attn, self).__init__()
        self.method = method
        self.hidden_size = hidden_size
        self.attn = nn.Linear(self.hidden_size * 2, hidden_size)
        self.v = nn.Parameter(torch.rand(hidden_size))
        stdv = 1. / math.sqrt(self.v.size(0))
        self.v.data.normal_(mean=0, std=stdv)

    def forward(self, hidden, encoder_outputs):
        """
        :param hidden:
            previous hidden state of the decoder, in shape (layers*directions, B, H)
        :param encoder_outputs:
            encoder outputs from Encoder, in shape (T, B, H)
        :return
            attention energies in shape (B, T)
        """

        max_len = encoder_outputs.size(1)  # check dimensions, len dimension
        this_batch_size = encoder_outputs.size(0)  # batch size dimensio
        H = hidden.repeat(max_len, 1, 1).transpose(0, 1)  # Repeat hidden from decoder
        attn_score = self.score(H, encoder_outputs)  # compute attention score
        return F.softmax(attn_score, dim=1).unsqueeze(1)  # normalize with softmax - attn weights, check dimensions

    def score(self, hidden, encoder_outputs):
        energy = F.tanh(self.attn(torch.cat([hidden, encoder_outputs], 2)))  # [B*T*2H]->[B*T*H]
        energy = energy.transpose(2, 1)  # [B*H*T] - This is probably wrong!
        v = self.v.repeat(encoder_outputs.data.shape[0], 1).unsqueeze(1)  # [B*1*H]
        energy = torch.bmm(v, energy)  # [B*1*T]
        return energy.squeeze(1)


class Decoder(nn.Module):
    def __init__(self, input_size, output_size, number_steps_predict, num_layers=1, hidden_size=10, cell_type='LSTM'):
        super(Decoder, self).__init__()

        self.number_steps_predict = number_steps_predict

        assert cell_type in ['LSTM', 'RNN', 'GRU'], 'RNN type is not supported'

        if cell_type == 'LSTM':
            self.decoder_cell = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        if cell_type == 'GRU':
            self.decoder_cell = nn.GRU(input_size, hidden_size, num_layers, batch_first=True)
        if cell_type == 'RNN':
            self.decoder_cell = nn.RNN(input_size, hidden_size, num_layers, batch_first=True)

        self.output_layer = nn.Linear(hidden_size, output_size)

    def forward(self, x, hidden_state):
        output, hidden_state = self.decoder_cell(x, hidden_state)

        outputs = []
        for step in range(self.number_steps_predict):
            outputs.append(self.output_layer(output[:, step, :]))
        return torch.stack(outputs, dim=1), hidden_state

    def forward_attention(self, x, hidden_state):
        output, hidden_state = self.decoder_cell(x, hidden_state)
        output = self.output_layer(output[:, 0, :])
        return output, hidden_state

    def predict(self, x, hidden_state):
        preds = []
        pred = x
        for step in range(self.number_steps_predict):
            output, hidden_state = self.decoder_cell(pred, hidden_state)

            pred = self.output_layer(output)
            preds.append(pred)
        return torch.stack(preds, dim=1)[:, :, 0, 0]

    def predict_attention(self, x, hidden_state):
        output, hidden_state = self.decoder_cell(x, hidden_state)
        output = self.output_layer(output[:, 0, :])
        return output, hidden_state

    def predict_generating(self, x, hidden_state, predict_steps):
        preds = []
        pred = x
        for step in range(predict_steps):
            output, hidden_state = self.decoder_cell(pred, hidden_state)
            pred = self.output_layer(output)
            preds.append(pred)
        return torch.stack(preds, dim=1)[:, :, 0, 0]


class EncoderDecoder(nn.Module):
    def __init__(self,
                 number_features_encoder,
                 number_features_decoder,
                 number_steps_predict,
                 hidden_size_encoder,
                 hidden_size_decoder,
                 num_layers,
                 cell_type_encoder,
                 cell_type_decoder,
                 number_features_output,
                 use_attention=False):
        super(EncoderDecoder, self).__init__()

        self.use_attention = use_attention
        self.number_steps_predict = number_steps_predict
        self.encoder = Encoder(number_features_encoder, num_layers, hidden_size_encoder, cell_type_encoder)
        self.decoder = Decoder(number_features_decoder, number_features_output, number_steps_predict, num_layers,
                               hidden_size_decoder, cell_type_decoder)
        if use_attention:
            self.decoder = Decoder((number_features_decoder + hidden_size_decoder), number_features_output,
                                   number_steps_predict, num_layers, hidden_size_decoder, cell_type_decoder)
            self.attention = Attn('concat', hidden_size_encoder)

    def forward(self):
        pass

    def train_step(self, X_encoder, X_decoder):

        if self.use_attention:
            output, hidden = self.encoder(X_encoder)
            hidden_decoder = hidden
            predictions = []
            for step in range(self.number_steps_predict):
                input_decoder = X_decoder[:, step, :]
                input_decoder = input_decoder.unsqueeze(1)
                if self.encoder.cell_type == 'LSTM':
                    attn_weights = self.attention(hidden_decoder[0][-1], output)  # hidden_state -1 ou 1
                else:
                    attn_weights = self.attention(hidden_decoder[-1], output)  # hidden_state -1 ou 1
                context = attn_weights.bmm(output)  # (B,1,V)
                input_decoder = torch.cat((input_decoder, context), 2)
                output_decoder, hidden_decoder = self.decoder.forward_attention(input_decoder, hidden_decoder)
                predictions.append(output_decoder)
            predictions = torch.stack(predictions, dim=1)[:, :, 0]
        else:
            output, hidden = self.encoder(X_encoder)

            predictions, hidden_decoder = self.decoder(X_decoder, hidden)
        return predictions, hidden_decoder

    def predict(self, X_encoder, X_decoder):

        if self.use_attention:
            output, hidden = self.encoder(X_encoder)
            hidden_decoder = hidden
            predictions = []
            input_decoder = X_decoder[:, 0, :]
            for step in range(self.number_steps_predict):
                input_decoder = input_decoder.unsqueeze(1)
                if self.encoder.cell_type == 'LSTM':
                    attn_weights = self.attention(hidden_decoder[0][-1], output)  # hidden_state -1 ou 1
                else:
                    attn_weights = self.attention(hidden_decoder[-1], output)
                context = attn_weights.bmm(output)  # (B,1,V)
                input_decoder = torch.cat((input_decoder, context), 2)
                input_decoder, hidden_decoder = self.decoder.forward_attention(input_decoder, hidden_decoder)
                predictions.append(input_decoder)
            predictions = torch.stack(predictions, dim=1)[:, :, 0]
        else:
            output, hidden = self.encoder(X_encoder)
            predictions = self.decoder.predict(X_decoder, hidden)

        return predictions


class EncoderDecoderTrainer(Trainer):
    def __init__(self,
                 lr,
                 number_steps_train,
                 number_steps_predict,
                 hidden_size_encoder,
                 hidden_size_decoder,
                 num_layers,
                 cell_type_encoder,
                 cell_type_decoder,
                 use_attention,
                 target_column,
                 batch_size,
                 num_epoch,
                 number_features_encoder=1,
                 number_features_decoder=1,
                 number_features_output=1,
                 loss_function='MSE',
                 optimizer='Adam',
                 normalizer='Standardization',
                 use_scheduler=False,
                 validation_date=None,
                 test_date=None,
                 **kwargs):
        """
        Trainer class for encoder-decoder models

        Parameters
        ----------
        lr : float

        number_steps_train : int
            Sequence length for training

        number_steps_predict : int
            Sequence length for predict

        hidden_size_encoder : int

        hidden_size_decoder : int

        num_layers : int

        cell_type_encoder : str
            model to implement in encoder

        cell_type_decoder : str
            model to implement in decoder

        use_attention : boolean, default, False
            If True use attention system

        target_column : str
            Column to predict

        batch_size : int

        num_epoch : int

        number_features_encoder : int

        number_features_decoder : int

        number_features_output : int

        loss_function : str, default : Adam
            Loss function to use. Currently implemented : MSE, MAE

        optimizer : str, default : MSE
            Optimizer to use. Currently implemented : Adam, SGD, RMSProp, Adadelta, Adagrad

        normalizer : str, default : Standardization
            Normalizer for the data

        use_scheduler : boolean, default : False
            If True use learning rate scheduler

        validation_date : int or datetime
            Validation split

        test_date : int or datetime
            Test split

        kwargs : **
        """

        super(EncoderDecoderTrainer, self).__init__(**kwargs)

        torch.manual_seed(SEED)

        # Hyper-parameters
        self.number_steps_train = number_steps_train
        self.number_steps_predict = number_steps_predict
        self.lr = lr
        self.batch_size = batch_size
        self.num_epoch = num_epoch
        self.use_scheduler = use_scheduler
        self.target_column = target_column
        self.hidden_size_encoder = hidden_size_encoder
        self.hidden_size_decoder = hidden_size_decoder
        self.num_layers = num_layers
        self.use_attention = use_attention
        self.cell_type_encoder = cell_type_encoder
        self.cell_type_decoder = cell_type_decoder
        self.number_features_encoder = number_features_encoder
        self.number_features_decoder = number_features_decoder
        self.number_features_output = number_features_output
        self.loss_function = loss_function
        self.optimizer = optimizer
        self.normalizer = normalizer
        self.validation_date = validation_date
        self.test_date = test_date

        self.file_name = self.filelogger.file_name

        self.train_generator = None
        self.validation_generator = None
        self.test_generator = None

        # Save metadata model
        metadata_key = ['number_steps_train',
                        'number_steps_predict',
                        'hidden_size',
                        'num_layers',
                        'cell_type',
                        'use_attention'
                        'lr',
                        'batch_size',
                        'num_epoch',
                        'target_column',
                        'validation_date',
                        'test_date']

        metadata_value = [self.number_steps_train,
                          self.number_steps_predict,
                          self.hidden_size_encoder,
                          self.num_layers,
                          self.cell_type_decoder,
                          self.use_attention,
                          self.lr,
                          self.batch_size,
                          self.num_epoch,
                          self.target_column,
                          self.validation_date,
                          self.test_date]

        metadata_dict = {}
        for i in range(len(metadata_key)):
            metadata_dict[metadata_key[i]] = metadata_value[i]

        # check if it's to load model or not
        if self.filelogger.load_model is not None:
            self.load(self.filelogger.load_model)
            print('Load model from {}'.format(
                self.logger_path + self.file_name + 'model_checkpoint/' + self.filelogger.load_model))
        else:
            self.model = EncoderDecoder(self.number_features_encoder,
                                        self.number_features_decoder,
                                        self.number_steps_predict,
                                        self.hidden_size_encoder,
                                        self.hidden_size_decoder,
                                        self.num_layers,
                                        self.cell_type_encoder,
                                        self.cell_type_decoder,
                                        self.number_features_output,
                                        self.use_attention)

            self.filelogger.write_metadata(metadata_dict)

            # loss function
            if loss_function == 'MSE':
                self.criterion = nn.MSELoss()
            # optimizer
            if optimizer == 'Adam':
                self.model_optimizer = optim.Adam(self.model.parameters(), lr=self.lr)
            if optimizer == 'SGD':
                self.model_optimizer = optim.SGD(self.model.parameters(), lr=self.lr)
            if optimizer == 'RMSProp':
                self.model_optimizer = optim.RMSprop(self.model.parameters(), lr=self.lr)
            if optimizer == 'Adadelta':
                self.model_optimizer = optim.Adadelta(self.model.parameters(), lr=self.lr)
            if optimizer == 'Adagrad':
                self.model_optimizer = optim.Adagrad(self.model.parameters(), lr=self.lr)

            if self.use_scheduler:
                self.scheduler = ReduceLROnPlateau(self.model_optimizer, 'min', patience=2, threshold=1e-5)

            # check CUDA availability
            if self.use_cuda:
                self.model.cuda()

    @staticmethod
    def init_weights(m):
        if type(m) in [nn.LSTM, nn.GRU, nn.RNN]:
            for name, param in m.named_parameters():
                if 'bias' in name:
                    nn.init.constant(param, 0.00)
                elif 'weight' in name:
                    nn.init.xavier_normal(param)
        if type(m) in [nn.Linear, nn.Conv1d]:
            torch.nn.init.xavier_uniform(m.weight)
            m.bias.data.fill_(0.00)

    def prepare_datareader(self):
        # prepare datareader
        self.datareader.preprocessing_data(self.number_steps_train,
                                           self.number_steps_predict,
                                           self.batch_size,
                                           self.validation_date,
                                           self.test_date,
                                           self.normalizer)
        # Initialize train generator
        self.train_generator = self.datareader.generator_train(self.batch_size,
                                                               self.target_column,
                                                               allow_smaller_batch=True)

        # Initialize validation and test generator
        if self.validation_date is not None:
            self.validation_generator = self.datareader.generator_validation(self.batch_size,
                                                                             self.target_column)

        if self.test_date is not None:
            self.test_generator = self.datareader.generator_test(self.batch_size,
                                                                 self.target_column)

    def prepare_datareader_cv(self,
                              cv_train,
                              cv_val):
        # prepare datareader
        self.datareader.preprocessing_data_cv(self.number_steps_train,
                                              self.number_steps_predict,
                                              self.batch_size,
                                              cv_train,
                                              cv_val,
                                              self.normalizer)
        # Initialize train generator
        self.train_generator = self.datareader.generator_train(self.batch_size,
                                                               self.target_column,
                                                               allow_smaller_batch=True)

        if self.validation_date is not None:
            self.validation_generator = self.datareader.generator_validation(self.batch_size,
                                                                             self.target_column)

    def training_step(self):

        self.model_optimizer.zero_grad()
        X, Y = next(self.train_generator)
        length = X.shape[0]
        X = Variable(torch.from_numpy(X)).float().cuda()
        Y = Variable(torch.from_numpy(Y)).float()
        temp_array = np.empty((Y.shape[0], 1))
        temp_array.fill(-100)
        temp = Variable(torch.from_numpy(temp_array)).float()
        decoder_input = torch.cat((temp, Y), dim=1)[:, :-1].unsqueeze(2)
        results, _ = self.model.train_step(X, decoder_input.cuda())
        loss = self.criterion(results, Y.unsqueeze(2).cuda())
        loss.backward()
        self.model_optimizer.step()

        return loss.data[0], loss.data[0] * length

    def evaluation_step(self):

        X, Y = next(self.validation_generator)
        length = X.shape[0]
        X = Variable(torch.from_numpy(X), requires_grad=False, volatile=True).float().cuda()
        Y = Variable(torch.from_numpy(Y), requires_grad=False, volatile=True).float().cuda()
        temp_array = np.empty((Y.shape[0], 1))
        temp_array.fill(-100)
        decoder_input = Variable(torch.from_numpy(temp_array), requires_grad=False, volatile=True).float()
        results = self.model.predict(X, decoder_input.unsqueeze(1).cuda())
        valid_loss = self.criterion(results, Y.unsqueeze(2).cuda())

        return valid_loss.data[0], valid_loss.data[0] * length

    def prediction_step(self):

        X, Y = next(self.test_generator)
        X = Variable(torch.from_numpy(X), requires_grad=False, volatile=True).float().cuda()
        temp_array = np.empty((Y.shape[0], 1))
        temp_array.fill(-100)
        decoder_input = Variable(torch.from_numpy(temp_array), requires_grad=False, volatile=True).float()
        results = self.model.predict(X, decoder_input.unsqueeze(2).cuda())

        return results, Y
