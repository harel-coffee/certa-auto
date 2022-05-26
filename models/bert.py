import logging
import os
import string
import random
import pandas as pd
import numpy as np
from scipy.sparse import csr_matrix

import torch
from torch.utils.data import DataLoader

import models.emt.model
import models.emt.config
import models.emt.data_representation
import models.emt.data_loader
import models.emt.optimizer
import models.emt.evaluation
import models.emt.torch_initializer
import models.emt.training
import models.emt.prediction
from models.ermodel import ERModel
from models.ditto.ditto import DittoModel, DittoDataset

BATCH_SIZE = 8

MAX_SEQ_LENGTH = 250


def emt_mojito_predict(model):
    def wrapper(dataframe):
        dataframe['id'] = np.arange(len(dataframe))
        output = model.predict(dataframe)
        return np.dstack((output['nomatch_score'], output['match_score'])).squeeze()

    return wrapper


class EMTERModel(ERModel):

    def __init__(self, ditto=True):
        self.name = 'bert'
        self.ditto = ditto
        super(EMTERModel, self).__init__()
        self.model_type = 'distilbert'
        config_class, model_class, tokenizer_class = models.emt.config.Config().MODEL_CLASSES[self.model_type]
        config = config_class.from_pretrained('distilbert-base-uncased')
        self.tokenizer = tokenizer_class.from_pretrained('distilbert-base-uncased', do_lower_case=True)
        device, n_gpu = models.emt.torch_initializer.initialize_gpu_seed(22)
        if self.ditto:
            self.model = DittoModel(lm='distilbert', device=device)
        else:
            self.model = model_class.from_pretrained('distilbert-base-uncased', config=config)

    def train(self, label_train, label_valid, dataset_name, epochs=7):
        try:
            device, n_gpu = models.emt.torch_initializer.initialize_gpu_seed(22)
            self.model = self.model.to(device)
        except:
            pass

        exp_dir = 'saved/bert/' + dataset_name
        if len(label_train) > 0:
            print('training is about to start')
            # balanced datasets
            # g_train = label_train.groupby('label')
            # label_train = pandas.DataFrame(g_train.apply(lambda x: x.sample(g_train.size().min()).reset_index(drop=True)))
            processor = models.emt.data_representation.DeepMatcherProcessor()
            # trainF, validF = dm_train.tofiles(label_train, label_valid, dataset_name)
            trainF = dataset_name + '_train.csv'
            validF = dataset_name + '_valid.csv'
            label_train.to_csv(trainF)
            label_valid.to_csv(validF)
            train_examples = processor.get_train_examples_file(trainF)
            label_list = processor.get_labels()
            training_data_loader = models.emt.data_loader.load_data(train_examples,
                                                             label_list,
                                                             self.tokenizer,
                                                             MAX_SEQ_LENGTH,
                                                             BATCH_SIZE,
                                                             models.emt.data_loader.DataType.TRAINING, self.model_type)
            num_epochs = epochs
            num_train_steps = len(training_data_loader) * num_epochs

            learning_rate = 2e-5
            adam_eps = 1e-8
            warmup_steps = 1
            weight_decay = 0
            optimizer, scheduler = models.emt.optimizer.build_optimizer(self.model,
                                                                 num_train_steps,
                                                                 learning_rate,
                                                                 adam_eps,
                                                                 warmup_steps,
                                                                 weight_decay)
            eval_examples = processor.get_test_examples_file(validF)
            evaluation_data_loader = models.emt.data_loader.load_data(eval_examples,
                                                               label_list,
                                                               self.tokenizer,
                                                               MAX_SEQ_LENGTH,
                                                               BATCH_SIZE,
                                                               models.emt.data_loader.DataType.EVALUATION, self.model_type)

            evaluation = models.emt.evaluation.Evaluation(evaluation_data_loader, '', exp_dir, len(label_list), self.model_type)

            result = models.emt.training.train(device,
                               training_data_loader,
                               self.model,
                               optimizer,
                               scheduler,
                               evaluation,
                               num_epochs,
                               1.0,
                               True,
                               experiment_name=exp_dir,
                               output_dir=exp_dir,
                               model_type=self.model_type)

        models.emt.model.save_model(self.model, '', exp_dir, tokenizer=self.tokenizer)
        logging.info('MODEL SAVED {}', exp_dir)
        return result

    def evaluation(self, test_set):
        device, n_gpu = models.emt.torch_initializer.initialize_gpu_seed(22)
        processor = models.emt.data_representation.DeepMatcherProcessor()
        tmpf = 'tmp.csv'
        test_set.to_csv(tmpf)
        examples = processor.get_test_examples_file(tmpf)
        test_data_loader = models.emt.data_loader.load_data(examples,
                                                     processor.get_labels(),
                                                     self.tokenizer,
                                                     MAX_SEQ_LENGTH,
                                                     BATCH_SIZE,
                                                     models.emt.data_loader.DataType.EVALUATION, self.model_type)


        evaluation = models.emt.evaluation.Evaluation(test_data_loader, '', '', len(test_set),
                                               self.model_type)

        result = evaluation.evaluate(self.model, device, -1)
        try:
            l0 = result.split('\n')[2].split('       ')[2].split('      ')
            l1 = result.split('\n')[3].split('       ')[2].split('      ')
        except:
            l0 = result['report'].split('\n')[2].split('       ')[2].split('      ')
            l1 = result['report'].split('\n')[3].split('       ')[2].split('      ')

        p = l1[0]
        r = l1[1]
        f1 = l1[2]
        pnm = l0[0]
        rnm = l0[1]
        f1nm = l0[2]

        os.remove(tmpf)

        return float(p), float(r), float(f1)

    def predict(self, x, given_columns=None, mojito=False, expand_dim=False, **kwargs):
        if isinstance(x, csr_matrix):
            x = pd.DataFrame(data=np.zeros(x.shape))
            if given_columns is not None:
                x.columns = given_columns
        original = x.copy()
        if isinstance(x, np.ndarray):
            x_index = np.arange(len(x))
            xc = pd.DataFrame(x.copy(), index=x_index)
        else:
            xc = x.copy()
        if 'id' in xc.columns:
            xc = xc.drop(['id'], axis=1)
        if 'ltable_id' in xc.columns:
            xc = xc.drop(['ltable_id'], axis=1)
        if 'rtable_id' in xc.columns:
            xc = xc.drop(['rtable_id'], axis=1)
        if 'label' not in xc.columns:
            xc.insert(0, 'label', '')
        device, n_gpu = models.emt.torch_initializer.initialize_gpu_seed(22)
        if self.ditto:
            inputs = []
            for idx in range(len(xc)):
                tup = xc.iloc[idx]
                l = ''
                r = ''
                for c in xc.columns:
                    if c.startswith('ltable'):
                        l += str(tup[c]) + ' '
                    else:
                        r += str(tup[c]) + ' '
                inputs.append(l +'\t' + r +'\t0')
            dataset = DittoDataset(inputs,
                                   max_len=256,
                                   lm=self.model_type)
            iterator = DataLoader(dataset=dataset,
                                       batch_size=len(dataset),
                                       shuffle=False,
                                       num_workers=0,
                                       collate_fn=DittoDataset.pad)
            # prediction
            all_probs = []
            all_logits = []
            with torch.no_grad():
                for i, batch in enumerate(iterator):
                    x_in, _ = batch
                    logits = self.model(x_in)
                    probs = logits.softmax(dim=1)[:, 1]
                    all_probs += probs.cpu().numpy().tolist()
                    all_logits += logits.cpu().numpy().tolist()

            threshold = 0.5

            pred = [1 if p > threshold else 0 for p in all_probs]
            xc['match_score'] = all_probs
            xc['nomatch_score'] = 1 - xc['match_score']
            if 'id' in x.columns:
                xc['id'] = x['id']
            if 'ltable_id' in x.columns:
                xc['ltable_id'] = x['ltable_id']
            if 'rtable_id' in x.columns:
                xc['rtable_id'] = x['rtable_id']
            if 'label' in x.columns:
                xc['label'] = x['label']
            return xc
        else:
            processor = models.emt.data_representation.DeepMatcherProcessor()
            tmpf = "./{}.csv".format("".join([random.choice(string.ascii_lowercase) for _ in range(10)]))
            xc.to_csv(tmpf)
            examples = processor.get_test_examples_file(tmpf)
            test_data_loader = models.emt.data_loader.load_data(examples,
                                                         processor.get_labels(),
                                                         self.tokenizer,
                                                         MAX_SEQ_LENGTH,
                                                         BATCH_SIZE,
                                                         models.emt.data_loader.DataType.TEST, self.model_type)

            simple_accuracy, f1, classification_report, predictions = models.emt.prediction.predict(self.model, device,
                                                                                             test_data_loader)
            os.remove(tmpf)

            predictions.index = np.arange(len(predictions))
            if mojito:
                full_df = np.dstack((predictions['nomatch_score'], predictions['match_score'])).squeeze()
                res_shape = full_df.shape
                if len(res_shape) == 1 and expand_dim:
                    full_df = np.expand_dims(full_df, axis=1).T
            else:
                names = list(xc.columns)
                names.extend(['classes', 'labels', 'nomatch_score', 'match_score'])
                xc.index = np.arange(len(xc))
                full_df = pd.concat([xc, predictions], axis=1, names=names)
                full_df.columns = names
                try:
                    original.reset_index(inplace=True)
                    full_df['ltable_id'] = original['ltable_id']
                    full_df['rtable_id'] = original['rtable_id']
                    full_df['id'] = original['id']
                except:
                    pass
            return full_df

    def load(self, path):
        self.model, self.tokenizer = models.emt.model.load_model(path, True)
        device, n_gpu = models.emt.torch_initializer.initialize_gpu_seed(22)
        self.model = self.model.to(device)
        return self.model

    def save(self, path):
        models.emt.model.save_model(self.model, '', path, tokenizer=self.tokenizer)

    def predict_proba(self, x, **kwargs):
        return self.predict(x, mojito=True, expand_dim=True)
