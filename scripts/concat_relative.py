""" generate `concat_relative_fasttext` embedding model """
import argparse
import os
import logging
import truecase
from tqdm import tqdm
from gensim.models import KeyedVectors
from util import get_word_embedding_model

logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')


def get_options():
    parser = argparse.ArgumentParser(description='concat RELATIVE and word embedding')
    parser.add_argument('-o', '--output-dir', help='Output file path to store relation vectors',
                        type=str, default="./cache")
    parser.add_argument('--model', help='word embedding model', type=str, default="fasttext")
    parser.add_argument('--truecase', help='Truecasing', action='store_true')
    return parser.parse_args()


def tc(string):
    return truecase.get_true_case('A ' + string)[2:]


if __name__ == '__main__':
    opt = get_options()

    model_word = get_word_embedding_model(opt.model)

    cache = '{}/relative_init.{}.txt'.format(opt.output_dir, opt.model)
    relative_model = 'relative_init.{}'.format(opt.model)
    if opt.truecase:
        cache = '{}/relative_init.{}.truecase.txt'.format(opt.output_dir, opt.model)
        relative_model += '.truecase'

    model = get_word_embedding_model(relative_model)

    logging.info("concat with word embedding model")
    cache_concat = cache.replace('.txt', '.concat.txt')
    cache_concat_bin = cache_concat.replace('.txt', '.bin')
    if os.path.exists(cache_concat):
        os.remove(cache_concat)

    vocab_size = 0
    with open(cache_concat + '.tmp', 'w') as f:
        for v in tqdm(model.vocab):
            a, b = v.split('__')
            if opt.truecase:
                a, b = tc(a), tc(b)
            try:
                v_diff = model_word[a] - model_word[b]
                new_vector = list(model[v]) + list(v_diff)
                f.write(v + ' ' + ' '.join([str(i) for i in new_vector]) + "\n")
                vocab_size += 1
            except Exception:
                pass
    with open(cache_concat, 'w') as f:
        f.write(str(vocab_size) + " " + str(model.vector_size + model_word.vector_size) + "\n")
        with open(cache_concat + '.tmp', 'r') as f_read:
            for line in f_read:
                f.write(line)

    logging.info("producing binary file")
    model = KeyedVectors.load_word2vec_format(cache_concat)
    model.wv.save_word2vec_format(cache_concat_bin, binary=True)
    logging.info("new embeddings are available at {}".format(cache_concat_bin))



