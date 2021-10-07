import os
import argparse
import time
import pickle
from tqdm import tqdm
import pandas as pd
from multiprocessing import Pool
from helper_scripts.wit_url_downloader import download_wit_urls
from helper_scripts.wit_clip_class import CLIP
from helper_scripts.wit_dtype import DTYPE
from helper_scripts.wit_image_downloader import wit_download_image
import multiprocessing

os.environ['KMP_DUPLICATE_LIB_OK']='True'

CHUNKSIZE = 64
MULTIPROCESSING = True
THREAD_COUNT = multiprocessing.cpu_count()
CHUNKSIZE = THREAD_COUNT*10000
SIMILARITIESFOLDER = './wit/witsimilarities'
EMBEDDINGSFOLDER = './wit/witembeddings'
WITURLFOLDER = './wit/witurls'
EMBEDDINGS_PER_PICKLE = 10000

clipper = CLIP()

parser = argparse.ArgumentParser()

parser.add_argument('--wit_url_folder', type=str,
                    help='Download location for WIT urls.')

parser.add_argument('--onepercentsample', 
                    dest='onepercentsample', 
                    action='store_true',
                    help='Only download 1% sample file.')

parser.add_argument('--saveimages', 
                    dest='saveimages', 
                    action='store_true',
                    help='Save the images on the local drive.')

parser.add_argument('--saveembeddings', 
                    dest='saveembeddings', 
                    action='store_true',
                    help='Save the image embeddings on the local drive.')

parser.add_argument('--savewds', 
                    dest='savewds', 
                    action='store_true',
                    help='Save the images and best matching caption as WebDataset')

args = parser.parse_args()

wit_url_folder = args.wit_url_folder if args.wit_url_folder else WITURLFOLDER

os.makedirs(SIMILARITIESFOLDER, exist_ok=True)
if args.saveembeddings:
    os.makedirs(EMBEDDINGSFOLDER, exist_ok=True)

dtv = list(DTYPE.keys())
caption_dict = {0:dtv[4], 1:dtv[5], 2:dtv[6], 3:dtv[7], 4:dtv[8], 5:dtv[15], 6:dtv[16]}

def process_row(row):
    saveembeddings = row[18]
    saveimages = row[19]
    image_url = row[3]
    captions = [
        row[5],  # row.page_title,
        row[6],  # row.section_title,
        row[7],  # row.hierarchical_section_title,
        row[8],  # row.caption_attribution_description,
        row[9],  # row.caption_alt_text_description,
        row[16], # row.context_page_description,
        row[17]  # row.context_section_description
    ]
    available_captions = [True if isinstance(x, str) else False for x in captions]
    caption_tuples = [(i, x) for i, x in enumerate(captions) if available_captions[i]]
    available_ids, captions = list(zip(*caption_tuples))

    try:
        image_request = wit_download_image(image_url, saveimages)

        similarities, embeddings = clipper.return_similarities(image_request, captions, image_url)
        similarities = {caption_dict[j]: round(similarities[i], 4) for i, j in enumerate(available_ids) }
    except Exception as e:
        print('Exception while trying to download {}'.format(image_url))
        print(e)
        return False, False, False
    else:
        if not saveembeddings:
            embeddings = None
            
        return row[0], similarities, embeddings

if __name__ == '__main__':
    start = time.time()
    global_counter = 0
    download_wit_urls(urlfolder=wit_url_folder, onepercentsample=args.onepercentsample)
    fns = [x for x in os.listdir(wit_url_folder) if x[0] != '.' and '.tsv.gz' in x]

    for i, wit_filename in enumerate(fns):
        print('Processing {}. file: {}...'.format(i+1, wit_filename))
        similarities_dict = {}
        embeddings_dict_counter = 0
        if args.saveembeddings:
            embeddings_dict = {}
            if '1percent' in wit_filename:
                prefix = "onepercent"
            else:
                prefix = 'main' + (wit_filename[-17])
        with pd.read_csv(
            os.path.join(wit_url_folder, wit_filename), 
            sep="\t", 
            compression="gzip", 
            chunksize=CHUNKSIZE,
            quotechar='"', 
            dtype=DTYPE,
            error_bad_lines=False
        ) as reader:
            for df in reader:
                df = df[df['language'] == 'en']
                df['saveembeddings'] = args.saveembeddings
                df['saveimages'] = args.saveimages
                embeddings_dict = {}
                results = []
                dflen = df.shape[0]

                if MULTIPROCESSING:
                    with Pool(THREAD_COUNT) as p:
                        res = tqdm(p.imap_unordered(process_row, df.itertuples(name=None), chunksize=CHUNKSIZE), total=dflen)
                        results.extend(res)
                        p.close()
                        # for _ in tqdm(p.imap_unordered(process_row, df.itertuples(name=None), chunksize=CHUNKSIZE), total=dflen):
                        #     pass

                    # with Pool(THREAD_COUNT) as p:
                    #     for result in tqdm(p.imap_unordered(process_row, df.itertuples(name=None)), total=dflen):
                    #         results.append(result)
                    #     p.close()
                else:
                    for row in tqdm(df.itertuples(name=None), total=dflen):
                        result = process_row(row)
                        results.append(result)          

                for result in results:
                    if result[0] != False:
                        index, sim, emb = result
                        similarities_dict[index] = sim
                        if args.saveembeddings:
                            embeddings_dict[index] = emb
                            if len(embeddings_dict.keys()) >= EMBEDDINGS_PER_PICKLE:
                                with open(os.path.join(
                                    EMBEDDINGSFOLDER, 
                                    '{}_{:05d}_image_embeddings.pkl'.format(prefix, embeddings_dict_counter)
                                ), 'wb') as f:
                                        pickle.dump(embeddings_dict, f)
                                        embeddings_dict_counter += 1

                global_counter += dflen

        similarity_df = pd.DataFrame.from_dict(similarities_dict, orient='index')
        similarity_df.index.name = 'index'
        similarity_df.index = similarity_df.index.astype(int)
        similarity_df = similarity_df.sort_index()
        similarity_df.to_csv(
            os.path.join(
                SIMILARITIESFOLDER, 
                wit_filename.replace('.tsv.gz', '') + '_with_similarities' + '.tsv'
            ), sep="\t")

    end = time.time()
    elapsed = end - start
    print('Finished processing {} WIT-rows in {} hours!'.format(global_counter, elapsed/60))