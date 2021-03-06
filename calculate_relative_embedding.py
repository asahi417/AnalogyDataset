""" Compute RELATIVE vector with Wikipedia dump
- wikidump is processed so that: lowercase, tokenizerd (token with multiple tokens is jointed by `_`)
- relative vocabulary is lowercase and token with multiple tokens is jointed by halfspace
"""
import logging
import os
import json
import pickle
import argparse
from itertools import groupby
from typing import Dict
from tqdm import tqdm

from gensim.models import KeyedVectors
from util import wget, get_word_embedding_model

logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')

# Corpus
URL_CORPUS = 'https://drive.google.com/u/0/uc?id=17EBy4GD4tXl9G4NTjuIuG5ET7wfG4-xa&export=download'
PATH_CORPUS = './cache/wikipedia_en_preprocessed.txt'
CORPUS_LINE_LEN = 104000000  # 53709029
if not os.path.exists(PATH_CORPUS):
    logging.info('downloading wikidump')
    wget(url=URL_CORPUS, cache_dir='./cache', gdrive_filename='wikipedia_en_preprocessed.zip')
OVERWRITE_CACHE = False

# Stopwords
with open('./stopwords_en.txt', 'r') as f:
    STOPWORD_LIST = list(set(list(filter(len, f.read().split('\n')))))


def get_wiki_vocab(minimum_frequency: int, word_vocabulary_size: int = None):
    """ Get word distribution over Wikidump (lowercased and tokenized) """
    dict_freq = {}
    bar = tqdm(total=CORPUS_LINE_LEN)
    with open(PATH_CORPUS, 'r', encoding='utf-8') as corpus_file:
        for _line in corpus_file:
            bar.update()
            tokens = _line.strip().split(" ")
            for token in tokens:
                if token in STOPWORD_LIST or "__" in token or token.isdigit():
                    continue
                # token = token.replace('_', ' ')  # wiki dump do this preprocessing
                dict_freq[token] = dict_freq[token] + 1 if token in dict_freq else 1

    # frequency filter
    dict_freq = sorted(dict_freq.items(), key=lambda x: x[0])
    n = 0
    for n, (_, freq) in enumerate(dict_freq):
        if freq >= minimum_frequency:
            break

    if word_vocabulary_size is not None:
        m = min(len(dict_freq), word_vocabulary_size + n)
        dict_freq = {k: v for k, v in dict_freq[n:m]}
    else:
        dict_freq = {k: v for k, v in dict_freq[n:]}
    return list(dict_freq.keys())


def frequency_filtering(vocab_corpus, dict_pairvocab, window_size, cache_jsonline):

    def get_context(i, tokens):
        """ get context with token `i` in `tokens`, returns list of tuple (token_j, [w_1, ...])"""
        try:
            # `dict_pairvocab` construct multi words with halfspace while wiki dump with '_', so here to fix
            # the mismatch
            tmp_vocab = dict_pairvocab[tokens[i].replace('_', ' ')]
        except KeyError:
            return None

        context_i_ = [(tokens[j], list(filter(lambda x: len(x) > 1, tokens[i + 1:j]))) for j in
                      range(i + 2, min(i + 1 + window_size, len(tokens))) if tokens[j].replace('_', ' ') in tmp_vocab]
        context_i_ = [(k_, v_) for k_, v_ in context_i_ if len(v_) > 1]
        if len(context_i_) == 0:
            return None
        context_i_ = sorted(context_i_)
        return dict([(k_, list(g)[0][1]) for k_, g in groupby(context_i_, key=lambda x: x[0])])

    logging.info('cache context word')
    if OVERWRITE_CACHE or not os.path.exists(cache_jsonline):
        bar = tqdm(total=CORPUS_LINE_LEN)
        with open(cache_jsonline, 'w') as f_jsonline:
            with open(PATH_CORPUS, 'r', encoding='utf-8') as corpus_file:
                for sentence in corpus_file:
                    bar.update()
                    token_list = sentence.strip().split(" ")
                    contexts = [(token_list[i_], get_context(i_, token_list)) for i_ in range(len(token_list))]
                    contexts = dict(filter(lambda x: x[1] is not None, contexts))
                    if len(contexts) > 0:
                        f_jsonline.write(json.dumps(contexts) + '\n')

    logging.info('aggregate over cache')
    if not os.path.exists(cache_jsonline.replace('.jsonl', '_org.json')):
        context_word_dict = {}
        bar = tqdm(total=CORPUS_LINE_LEN)
        with open(cache_jsonline, 'r') as f_jsonline:
            for contexts in f_jsonline:
                bar.update()
                contexts = json.loads(contexts)
                for token_i_, context_i in contexts.items():

                    try:
                        context_word_dict[token_i_]
                    except KeyError:
                        context_word_dict[token_i_] = {}

                    for k, v in context_i.items():
                        try:
                            context_word_dict[token_i_][k]
                        except KeyError:
                            context_word_dict[token_i_][k] = {}

                        for token in v:

                            try:
                                context_word_dict[token_i_][k][token] += 1
                            except KeyError:
                                context_word_dict[token_i_][k][token] = 1

        with open(cache_jsonline.replace('.jsonl', '_org.json'), 'w') as f_json:
            json.dump(context_word_dict, f_json)
    else:
        with open(cache_jsonline.replace('.jsonl', '_org.json'), 'r') as f_json:
            context_word_dict = json.load(f_json)

    logging.info('filtering vocab')
    vocab_corpus = set(vocab_corpus)

    def filter_vocab(_dict):
        new_key = set(_dict.keys()).intersection(vocab_corpus)
        _new_dict = {__k: _dict[__k] for __k in new_key}
        return _new_dict

    logging.info('filtering vocab 1st')
    context_word_dict = {k: {k_: filter_vocab(v_) for k_, v_ in v.items()} for k, v in context_word_dict.items()}
    logging.info('filtering vocab 2nd')
    context_word_dict = {k: {k_: v_ for k_, v_ in v.items() if len(v_) > 0} for k, v in context_word_dict.items()}
    logging.info('filtering vocab 3rd')
    context_word_dict = {k: v for k, v in context_word_dict.items() if len(v) > 0}

    return context_word_dict


def get_relative_init(output_path: str,
                      context_word_dict: Dict,
                      minimum_frequency_context: int,
                      word_embedding_type: str = 'fasttext'):
    """ Get RELATIVE vectors """
    logging.info("loading embeddings")
    word_embedding_model = get_word_embedding_model(word_embedding_type)

    line_count = 0
    with open(output_path + '.tmp', 'w', encoding='utf-8') as txt_file:
        for token_i, tokens_paired in tqdm(context_word_dict.items()):
            for token_j in tokens_paired:
                vector_pair = 0
                cont_pair = 0
                for token_co in context_word_dict[token_i][token_j]:
                    freq = context_word_dict[token_i][token_j][token_co]
                    if freq < minimum_frequency_context:
                        continue
                    try:
                        tmp = token_co.replace('_', ' ')
                        token_co_vector = word_embedding_model[tmp]
                        vector_pair += (freq * token_co_vector)
                        cont_pair += 1
                    except Exception:
                        pass
                if cont_pair != 0:
                    vector_pair = vector_pair/cont_pair
                    txt_file.write('__'.join([token_i, token_j]))
                    for y in vector_pair:
                        txt_file.write(' ' + str(y))
                    txt_file.write("\n")
                    line_count += 1

    logging.info("reformat file to add header")
    logging.info("\t * {} lines, {} dim".format(line_count, word_embedding_model.vector_size))
    with open(output_path, 'w') as f_out:
        f_out.write(str(line_count) + " " + str(word_embedding_model.vector_size) + "\n")
        with open(output_path + '.tmp', 'r') as f_cache:
            for line in f_cache:
                f_out.write(line)


def get_options():
    parser = argparse.ArgumentParser(description='simplified RELATIVE embedding training')
    parser.add_argument('-o', '--output-dir', help='Output file path to store relation vectors',
                        type=str, default="./cache")
    parser.add_argument('-m', '--model', help='anchor word embedding model', type=str, default="glove")
    # The following parameters are needed if contexts are not provided
    parser.add_argument('-w', '--window-size', help='Co-occurring window size', type=int, default=10)
    parser.add_argument('--minimum-frequency-context', default=1, type=int,
                        help='Minimum frequency of words between word pair: increasing the number can speed up the'
                             'calculations and reduce memory but we would recommend keeping this number low')
    # The following parameters are needed if pair vocabulary is not provided
    parser.add_argument('--minimum-frequency', help='Minimum frequency of words', type=int, default=5)
    return parser.parse_args()


if __name__ == '__main__':
    opt = get_options()
    os.makedirs(opt.output_dir, exist_ok=True)
    pair_vocab = []
    for url in ['https://github.com/asahi417/AnalogyTools/releases/download/0.0.0/analogy_test_dataset.tar.gz',
                'https://github.com/asahi417/AnalogyTools/releases/download/0.0.0/lexical_relation_dataset.tar.gz']:
        wget(url, opt.output_dir)
        path = '{}/{}/vocab.txt'.format(opt.output_dir, os.path.basename(url).replace('.tar.gz', ''))
        with open(path) as f:
            pair_vocab += [x.split('\t') for x in f.read().split('\n') if len(x)]
    # lower case vocab
    pair_vocab = [[i[0].lower(), i[1].lower()] for i in pair_vocab]
    # add backward
    pair_vocab += [[b, a] for a, b in pair_vocab]

    logging.info("extracting contexts(this can take a few hours depending on the size of the corpus)")
    logging.info("\t * loading word frequency dictionary")
    cache = '{}/vocab.pkl'.format(opt.output_dir)
    if os.path.exists(cache):
        with open(cache, 'rb') as fb:
            vocab = pickle.load(fb)
    else:
        vocab = get_wiki_vocab(minimum_frequency=opt.minimum_frequency)
        with open(cache, 'wb') as fb:
            pickle.dump(vocab, fb)

    logging.info("\t * filtering corpus by frequency")
    cache = '{}/pairs_context.json'.format(opt.output_dir)
    if os.path.exists(cache):
        with open(cache, 'r') as f:
            pairs_context = json.load(f)
    else:
        logging.info("retrieve pair and word vocabulary (dictionary)")
        pair_vocab = sorted(pair_vocab)
        grouper = groupby(pair_vocab, key=lambda x: x[0])
        pair_vocab_dict = {k: list(set(list(map(lambda x: x[1], g)))) for k, g in grouper}
        pairs_context = frequency_filtering(
            vocab,
            pair_vocab_dict,
            opt.window_size,
            cache_jsonline='{}/pairs_context_cache.jsonl'.format(opt.output_dir))
        with open(cache, 'w') as f:
            json.dump(pairs_context, f)

    cache = '{}/relative_init.{}.txt'.format(opt.output_dir, opt.model)

    logging.info("\t * computing relative-init vectors: {}".format(cache))
    if not os.path.exists(cache):
        get_relative_init(
            output_path=cache,
            context_word_dict=pairs_context,
            minimum_frequency_context=opt.minimum_frequency_context,
            word_embedding_type=opt.model)

    logging.info("producing binary file")
    cache_bin = cache.replace('.txt', '.bin')
    if not os.path.exists(cache_bin):
        model = KeyedVectors.load_word2vec_format(cache)
        model.wv.save_word2vec_format(cache_bin, binary=True)
        logging.info("new embeddings are available at {}".format(cache_bin))
        os.remove(cache)
