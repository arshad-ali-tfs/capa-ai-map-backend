# Third-party model notice

The bundled embedding model is [BAAI/bge-large-en-v1.5](https://huggingface.co/BAAI/bge-large-en-v1.5), revision `d4aa6901d3a41ba39fb536a557fa166f842b0e09`. Its original model card is retained alongside the checkpoint, tokenizer and Sentence Transformers configuration. The upstream model card declares the MIT license.

[licenses/BGE-MIT.txt](licenses/BGE-MIT.txt) retains the MIT copyright and permission notice from the model authors' [FlagEmbedding project](https://github.com/FlagOpen/FlagEmbedding/blob/master/LICENSE). This notice applies to the BGE component, not to CAPA.AI source exports, reference records or historical clustering artifacts.

Python packages and the spaCy language model are installed from the pinned dependency list and retain their respective upstream licenses. Dependency binaries are installed during setup or Docker build; they are not copied into this Git repository.
