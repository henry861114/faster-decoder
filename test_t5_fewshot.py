import jieba
import json

from bert4keras.tokenizers import Tokenizer

from snippets import AutoRegressiveDecoderV2
from models import *

max_c_len = 256
max_t_len = 32
config_path = '/data/pretrain/chinese_t5_pegasus_base/config.json'
checkpoint_path = '/data/pretrain/chinese_t5_pegasus_base/model.ckpt'
dict_path = '/data/pretrain/chinese_t5_pegasus_base/vocab.txt'

config = json.load(open(config_path))
num_hidden_layers = config['num_hidden_layers']
hidden_size = config['hidden_size']

tokenizer = Tokenizer(dict_path, do_lower_case=True, pre_tokenize=lambda s: jieba.cut(s, HMM=False))

t5_encoder = build_t5_encoder_model(config_path, checkpoint_path)
t5_decoder = build_t5_decoder_model(config_path, checkpoint_path)
t5_decoder_with_cache = build_t5_decoder_with_cache_model(config_path, checkpoint_path)

t5_decoder.summary()

class Inference(AutoRegressiveDecoderV2):
    """seq2seq解码器"""

    def __init__(self, encoder, decoder, with_cache=False, *args, **kwargs):
        self.encoder = encoder
        self.decoder = decoder
        self.with_cache = with_cache
        super(Inference, self).__init__(start_id=tokenizer._token_start_id, end_id=tokenizer._token_end_id,
                                      maxlen=max_t_len, *args, **kwargs)
        
    @AutoRegressiveDecoderV2.wraps(default_rtype='logits')
    def predict(self, inputs, output_ids, states, flag=None, with_cache=False):
        c_encoded = inputs[0]
        output_ids = np.array(output_ids, dtype=np.float32)
        batch_size = c_encoded.shape[0]
        cache_batch_size = self.cache_outputs[0].shape[0]
        # sample 时需要调整维度
        if flag is not None and len(flag) <= cache_batch_size:
            # 保留未完成的
            self.cache_outputs = [c[flag] for c in self.cache_outputs]    
            
        elif batch_size > cache_batch_size:
            self.cache_outputs = [c.repeat(batch_size, axis=0) for c in self.cache_outputs]

        if with_cache:
            # when use cache, only need the last output_id
            cur_inputs = [c_encoded, output_ids[:,-1:]] + self.cache_outputs
        else:
            cur_inputs = [c_encoded, output_ids]
            
        # build input/output names
        input_names = ['Input-Context', 'Decoder-Input-Token']
        output_names = ['Decoder-Output']

        if with_cache:
            # when use cache, need add cache inputs and cache outputs 
            for i in range(num_hidden_layers):
                input_names.append(f'Transformer-{i}-Key-Cache-Input')
                input_names.append(f'Transformer-{i}-Value-Cache-Input')
                output_names.append(f'Transformer-{i}-Cache-Output')

        ret = self.decoder.predict(cur_inputs)
        output_logits = ret[0] if with_cache else ret 
        if with_cache:
            # build cache
            output_caches = ret[1:]
            cur_cache = []

            # copy to feed k/v cache-inputs
            for c in output_caches:
                cur_cache.extend([c, c])
            self.cache_outputs = [np.concatenate([c, o], axis=1) for c, o in zip(self.cache_outputs, cur_cache)]

        return output_logits[:,-1]

    def generate(self, text, n=1, topk=1, topp=1):

        self.cache_outputs = [np.empty([1, 0, hidden_size], dtype=np.float32) for _ in range(num_hidden_layers * 2)]
        c_token_ids, _ = tokenizer.encode(text, maxlen=max_c_len)
        
        c_encoded = self.encoder.predict(np.array([c_token_ids]))[0]
        output_ids, losses = self.random_sample([c_encoded], n=n, topk=topk, topp=topp,  with_loss=True, with_cache=self.with_cache)  # 基于sample
        candidates = [tokenizer.decode(output_id) for output_id in output_ids]
        
        return candidates
    
inference = Inference(t5_encoder, t5_decoder, with_cache=False)
inference_with_cache = Inference(t5_encoder, t5_decoder_with_cache, with_cache=True)

sent = '''预训练任务模仿了PEGASUS的摘要式预训练。具体来说，假设一个文档有n个句子，我们从中挑出大约n/4个句子（可以不连续），使得这n/4个句子拼起来的文本，
          跟剩下的3n/4个句子拼起来的文本，最长公共子序列尽可能长，然后我们将3n/4个句子拼起来的文本视为原文，n/4个句子拼起来的文本视为摘要，
          通过这样的方式构成一个“(原文, 摘要)”的伪摘要数据对。'''
print(inference_with_cache.generate(sent))
print(inference.generate(sent))