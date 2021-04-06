import pandas as pd
import numpy as np
import os
import gensim.downloader as api
import models.DeepER as dp
from certa.local_explain import dataset_local
from certa.triangles_method import explainSamples
from certa.eval import expl_eval
import random

def merge_sources(table, left_prefix, right_prefix, left_source, right_source, copy_from_table, ignore_from_table):
    dataset = pd.DataFrame(columns={col: table[col].dtype for col in copy_from_table})
    ignore_column = copy_from_table + ignore_from_table

    for _, row in table.iterrows():
        leftid = row[left_prefix + 'id']
        rightid = row[right_prefix + 'id']

        new_row = {column: row[column] for column in copy_from_table}

        try:
            for id, source, prefix in [(leftid, left_source, left_prefix), (rightid, right_source, right_prefix)]:

                for column in source.keys():
                    if column not in ignore_column:
                        new_row[prefix + column] = source.loc[id][column]

            dataset = dataset.append(new_row, ignore_index=True)
        except:
            pass
    return dataset


def to_deeper_data(df: pd.DataFrame):
    res = []
    for r in range(len(df)):
        row = df.iloc[r]
        lpd = row.filter(regex='^ltable_')
        rpd = row.filter(regex='^rtable_')
        if 'label' in row:
            label = row['label']
            res.append((lpd.values.astype('str'), rpd.values.astype('str'), label))
        else:
            res.append((lpd.values.astype('str'), rpd.values.astype('str')))
    return res


def predict_fn(x, m, ignore_columns=['ltable_id', 'rtable_id', 'label']):
    data = to_deeper_data(x.drop([c for c in ignore_columns if c in x.columns], axis=1))
    out = dp.predict(data, model, embeddings_model, tokenizer)
    out_df = pd.DataFrame(out, columns=['nomatch_score', 'match_score'])
    out_df.index = x.index
    return pd.concat([x.copy(), out_df], axis=1)


def get_original_prediction(r1, r2):
    lprefix = 'ltable_'
    rprefix = 'rtable_'
    r1_df = pd.DataFrame(data=[r1.values], columns=r1.index)
    r2_df = pd.DataFrame(data=[r2.values], columns=r2.index)
    r1_df.columns = list(map(lambda col: lprefix + col, r1_df.columns))
    r2_df.columns = list(map(lambda col: rprefix + col, r2_df.columns))
    r1r2 = pd.concat([r1_df, r2_df], axis=1)
    r1r2['id'] = "0@" + str(r1r2[lprefix + 'id'].values[0]) + "#" + "1@" + str(r1r2[rprefix + 'id'].values[0])
    r1r2 = r1r2.drop([lprefix + 'id', rprefix + 'id'], axis=1)
    return predict_fn(r1r2, model)[['nomatch_score', 'match_score']].values[0]


root_datadir = 'datasets/'
generate_cf = False

for subdir, dirs, files in os.walk(root_datadir):
    for dir in dirs:
        if dir == 'temporary':
            continue
        print(f'working on {dir}')
        datadir = os.path.join(root_datadir, dir)

        lsource = pd.read_csv(datadir + '/tableA.csv')
        rsource = pd.read_csv(datadir + '/tableB.csv')
        gt = pd.read_csv(datadir + '/train.csv')
        valid = pd.read_csv(datadir + '/valid.csv')
        test = pd.read_csv(datadir + '/test.csv')

        train_df = merge_sources(gt, 'ltable_', 'rtable_', lsource, rsource, ['label'], [])
        train_df.to_csv('experiments/ia.csv')
        valid_df = merge_sources(valid, 'ltable_', 'rtable_', lsource, rsource, ['label'], ['id'])
        test_df = merge_sources(test, 'ltable_', 'rtable_', lsource, rsource, ['label'], [])

        if not os.path.exists('models/glove.6B.50d.txt'):
            word_vectors = api.load("glove-wiki-gigaword-50")
            word_vectors.save_word2vec_format('models/glove.6B.50d.txt', binary=False)

        embeddings_index = dp.init_embeddings_index('models/glove.6B.50d.txt')
        emb_dim = len(embeddings_index['cat'])
        embeddings_model, tokenizer = dp.init_embeddings_model(embeddings_index)
        model = dp.init_DeepER_model(emb_dim)

        model = dp.train_model_ER(to_deeper_data(train_df), model, embeddings_model, tokenizer)

        tmin = 0.5
        tmax = 0.5

        evals = pd.DataFrame()
        cf_evals = pd.DataFrame()
        for i in range(len(test_df)):
            #rand_row = train_df.iloc[random.randint(0, len(train_df) - 1)]
            rand_row = test_df.iloc[i]
            l_id = int(rand_row['ltable_id'])
            l_tuple = lsource.iloc[l_id]
            r_id = int(rand_row['rtable_id'])
            r_tuple = rsource.iloc[r_id]

            prediction = get_original_prediction(l_tuple, r_tuple)
            class_to_explain = np.argmax(prediction)

            label = rand_row["label"]
            print(f'({l_id}-{r_id}) -> pred={class_to_explain}, label={label}')

            # get triangle 'cuts' depending on the length of the sources
            up_bound = min(len(lsource), len(rsource))
            cuts = []
            for c in range(1):
                cuts.append((1+c)*int(up_bound / 100))

            for nt in cuts:
                print('running CERTA with nt='+str(nt))
                print(f'generating explanation')
                local_samples = dataset_local(l_tuple, r_tuple, model, lsource, rsource, datadir, tmin, tmax, predict_fn,
                                              num_triangles=nt, class_to_explain=class_to_explain, use_predict=True)
                if len(local_samples) > 2:
                    maxLenAttributeSet = len(l_tuple) - 1
                    explanation, flipped_pred, triangles = explainSamples(local_samples, [lsource, rsource], model,
                                                                          predict_fn, class_to_explain,
                                                                          maxLenAttributeSet, True)
                    print(explanation)
                    for exp in explanation:
                        e_attrs = exp.split('/')
                        e_score = explanation[exp]
                        expl_evaluation = expl_eval(class_to_explain, e_attrs, e_score, lsource, l_tuple, model, prediction, rsource,
                                                    r_tuple, predict_fn)
                        print(expl_evaluation.head())
                        expl_evaluation['t_requested'] = nt
                        expl_evaluation['t_obtained'] = len(triangles)
                        expl_evaluation['label'] = label
                        evals = evals.append(expl_evaluation, ignore_index=True)
                        os.makedirs('experiments/'+dir, exist_ok=True)
                        evals.to_csv('experiments/'+dir+'/deeper-eval.csv')
                    if len(triangles) > 0:
                        pd.DataFrame(triangles).to_csv('experiments/'+dir+'/deeper-tri_'+str(l_id)+'-'+str(r_id)+'_'+str(nt)+'_'+str(tmin)+'-'+str(tmax)+'.csv')

                    if generate_cf:
                        print(f'generating cf explanation')
                        try:
                            cf_class = abs(1 - int(class_to_explain))

                            local_samples_cf = dataset_local(l_tuple, r_tuple, model, lsource, rsource, datadir, tmin, tmax, predict_fn,
                                                          num_triangles=nt, class_to_explain=cf_class)

                            if len(local_samples_cf) > 2:
                                explanation_cf, flipped_pred_cf, triangles_cf = explainSamples(local_samples, [lsource, rsource], model, predict_fn,
                                                                                      cf_class, maxLenAttributeSet, True)
                                for exp_cf in explanation_cf:
                                    e_attrs = exp_cf.split('/')
                                    e_score = explanation_cf[exp_cf]
                                    cf_expl_evaluation = expl_eval(class_to_explain, e_attrs, e_score, lsource, l_tuple, model, prediction, rsource,
                                                                r_tuple, predict_fn)
                                    cf_expl_evaluation['t_requested'] = nt
                                    cf_expl_evaluation['t_obtained'] = len(triangles_cf)
                                    cf_expl_evaluation['label'] = label
                                    print(cf_expl_evaluation.head())
                                    cf_evals = cf_evals.append(cf_expl_evaluation, ignore_index=True)
                                    cf_evals.to_csv('experiments/ia-eval-cf.csv')
                                if len(triangles_cf) > 0:
                                    pd.DataFrame(triangles_cf).to_csv(
                                        'experiments/ia-tri_cf_' + str(l_id) + '-' + str(r_id) + '_' + str(nt) + '_' + str(
                                            tmin) + '-' + str(tmax) + '.csv')
                        except:
                            pass
        evals.to_csv("experiments/"+dir+"_eval_"+str(tmin)+'-'+str(tmax)+'.csv')
        if generate_cf:
            cf_evals.to_csv("experiments/"+dir+"_eval_cf_"+str(tmin)+'-'+str(tmax)+'.csv')