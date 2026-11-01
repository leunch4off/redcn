"""Isolated ONE-candidate Marian ZH→RU pilot; no active model integration.

Prepare an already downloaded, size-pinned official archive, convert it to CT2
int8, and compare the same seven public reverse-translation development probes.
The external caller must bound this process's wall time. No downloads occur
inside this script; raw weights and converted artifacts stay in the work cache.
"""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path, PurePosixPath
import resource
import re
import statistics
import sys
import time
import unicodedata
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from text_model_contract import content_sha,file_sha
from text_quality import translation_checks
from text_quality_probes import probes

MODEL='Helsinki-NLP/Tatoeba-Challenge/zho-rus/opus-2021-05-16'
URL='https://object.pouta.csc.fi/Tatoeba-MT-models/zho-rus/opus-2021-05-16.zip'
ARCHIVE_BYTES=283_528_302
LICENSE_URL='https://github.com/Helsinki-NLP/Tatoeba-Challenge/blob/master/models/LICENSE'
README_URL='https://github.com/Helsinki-NLP/Tatoeba-Challenge/blob/master/models/zho-rus/README.md'


def normalize_opus(text):
    """Apply the packaged preprocess.sh normalizer without executing its shell.

    Preserve the upstream fullwidth-one mapping literally for reproducibility;
    these frozen development probes use only ASCII digits.
    """
    replacements={'，':',','、':',','”':'"','“':'"','∶':':','：':':',
        '？':'?','《':'"','》':'"','）':')','！':'!','（':'(','；':';',
        '１':'"','」':'"','「':'"','０':'0','３':'3','２':'2','５':'5',
        '６':'6','９':'9','７':'7','８':'8','４':'4','～':'~',"’":"'",
        '…':'...','━':'-','〈':'<','〉':'>','【':'[','】':']','％':'%'}
    text=re.sub(r'[。．] *','. ',text)
    text=''.join(replacements.get(character,character) for character in text)
    text=''.join(' ' if character!='\n' and unicodedata.category(character).startswith('C')
        else character for character in text)
    return re.sub(' +',' ',text).strip(' ')


def check_archive(archive):
    """Validate every member before extracting any untrusted archive path."""
    with zipfile.ZipFile(archive) as source:
        entries=source.infolist()
        if not 1<=len(entries)<=200 or len({row.filename for row in entries})!=len(entries) or sum(row.file_size for row in entries)>1_500_000_000:
            raise ValueError('Unexpected model archive size or member count')
        for row in entries:
            path=PurePosixPath(row.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in row.filename
                    or ((row.external_attr>>16)&0o170000)==0o120000
                    or row.flag_bits&1 or row.file_size>800_000_000):
                raise ValueError('Unsafe model archive member')
        return [{'name':row.filename,'bytes':row.file_size,'crc':row.CRC} for row in entries]


def safe_model_paths(raw):
    import yaml
    decoders=list(Path(raw).rglob('decoder.yml'))
    if len(decoders)!=1:raise ValueError('Expected exactly one decoder.yml')
    model_dir=decoders[0].parent
    decoder=yaml.safe_load(decoders[0].read_text())
    for relative in [*decoder.get('models',[]),*decoder.get('vocabs',[])]:
        candidate=(model_dir/relative).resolve()
        if not candidate.is_relative_to(model_dir.resolve()) or not candidate.is_file():
            raise ValueError('Decoder references a missing or external file')
    source,target=model_dir/'source.spm',model_dir/'target.spm'
    if not source.is_file() or not target.is_file():raise ValueError('Expected source.spm and target.spm')
    return model_dir,source,target


def prepare(args):
    work=Path(args.workdir).resolve();work.mkdir(parents=True,exist_ok=True)
    receipt_path=work/'preparation.json'
    if receipt_path.is_file():
        receipt=json.loads(receipt_path.read_text())
        converted=work/'ct2-int8/model.bin'
        if receipt.get('model')!=MODEL or not converted.is_file() or file_sha(converted)!=receipt.get('convertedSha256'):
            raise ValueError('Existing candidate receipt does not match converted weights')
        return receipt
    archive=Path(args.archive)
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size!=ARCHIVE_BYTES:
        raise ValueError('Use the exact official 283528302-byte archive')
    entries=check_archive(archive);raw=work/'raw'
    if raw.exists():raise ValueError('Preserve partial preparation; use a new work directory')
    raw.mkdir()
    start=time.monotonic()
    with zipfile.ZipFile(archive) as source:
        source.extractall(raw)
    model_dir,source_spm,target_spm=safe_model_paths(raw)
    import ctranslate2
    from ctranslate2.converters import OpusMTConverter
    destination=work/'ct2-int8'
    OpusMTConverter(str(model_dir)).convert(str(destination),quantization='int8')
    receipt={'version':1,'model':MODEL,'sourceLanguage':'zh','targetLanguage':'ru',
        'upstreamUrl':URL,'archiveBytes':ARCHIVE_BYTES,'archiveSha256':file_sha(archive),
        'publishedSha256Available':False,'archiveMembers':entries,
        'license':'CC-BY-4.0','licenseUrl':LICENSE_URL,'licenseGitBlob':'2fb94156cd39cad415432aeb1ac5b38d5cb53e1e',
        'sourceReadmeUrl':README_URL,'sourceReadmeGitBlob':'ec1716c92ad8eee951ec2ff4562d342d02ed49ce',
        'attribution':'Helsinki-NLP / OPUS-MT / Jörg Tiedemann; Tatoeba Translation Challenge (2020.wmt-1.139)',
        'changes':'Converted official Marian archive to CTranslate2 int8 for an isolated development pilot.',
        'ctranslate2Version':ctranslate2.__version__,'convertedSha256':file_sha(destination/'model.bin'),
        'convertedBytes':(destination/'model.bin').stat().st_size,
        'modelDirectory':str(model_dir.relative_to(work)),'sourceSpm':str(source_spm.relative_to(work)),
        'targetSpm':str(target_spm.relative_to(work)),
        'sourceSpmSha256':file_sha(source_spm),'targetSpmSha256':file_sha(target_spm),
        'preparationSeconds':time.monotonic()-start,
        'preparationPeakRssBytes':int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*(1 if sys.platform=='darwin' else 1024),
        'automaticPromotion':False}
    receipt_path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    return receipt


def run(args):
    output=Path(args.output)
    if output.exists():raise ValueError('Use a new report filename to preserve previous measurements')
    cache=Path(args.cache)
    from learning_scheduler import active_imports,model_resource
    count=active_imports(cache)
    if count!=0:raise ValueError('Active imports or unknown state: postpone the pilot')
    with model_resource(cache,kind='foreground',timeout=3):
        preparation_reused=(Path(args.workdir)/'preparation.json').is_file()
        begin=time.monotonic();receipt=prepare(args)
        import ctranslate2
        import sentencepiece as spm
        work=Path(args.workdir).resolve()
        source=spm.SentencePieceProcessor(model_file=str(work/receipt['sourceSpm']))
        target=spm.SentencePieceProcessor(model_file=str(work/receipt['targetSpm']))
        load_start=time.monotonic()
        translator=ctranslate2.Translator(str(work/'ct2-int8'),device='cpu',compute_type='int8',
            inter_threads=1,intra_threads=2)
        load_seconds=time.monotonic()-load_start;rows=[];all_probes=probes()
        try:
            for probe in all_probes:
                if probe['task']!='zh-ru':
                    rows.append({'id':probe['id'],'task':probe['task'],'status':'unsupported'});continue
                if time.monotonic()-begin>=args.max_seconds:
                    rows.append({'id':probe['id'],'task':probe['task'],'status':'incomplete'});continue
                start=time.monotonic();tokens=source.encode(normalize_opus(probe['source']),out_type=str)
                translated=translator.translate_batch([tokens],beam_size=6,length_penalty=1,
                    max_decoding_length=200,max_input_length=512)
                hypothesis=translated[0].hypotheses[0]
                text=target.decode(hypothesis)
                checks=translation_checks(probe['source'],text,'zh-ru')
                limited=len(hypothesis)>=200
                if limited:checks['passed']=False;checks['issues'].append('Достигнут предел токенов')
                rows.append({'id':probe['id'],'task':probe['task'],'status':'incomplete' if limited else 'measured',
                    'text':text,'seconds':time.monotonic()-start,'checks':checks})
                print(json.dumps({'id':probe['id'],'passed':checks['passed'],'seconds':rows[-1]['seconds']},ensure_ascii=False),flush=True)
        finally:
            translator.unload_model()
        measured=[row for row in rows if row['status']=='measured']
        scale=1 if sys.platform=='darwin' else 1024
        result={'version':1,'status':'development-pilot-unaccepted','model':MODEL,'backend':'direct-marian-ct2-int8',
            'probeSha256':content_sha(all_probes),'activeImportsBefore':count,'preparation':receipt,
            'preprocessing':'Packaged preprocess.sh punctuation/control/spacing normalization then source.spm',
            'upstreamPreprocessorSha256':file_sha(work/receipt['modelDirectory']/'preprocess.sh'),
            'decoding':{'beamSize':6,'lengthPenalty':1,'maxDecodingLength':200,'maxInputLength':512},
            'rows':rows,'applicable':7,'measured':len(measured),
            'checksPassed':sum(row['checks']['passed'] for row in measured),
            'unsupported':11,'modelLoadSeconds':load_seconds,'modelReusedAcrossProbes':True,
            'preparationReused':preparation_reused,'scriptSha256':file_sha(Path(__file__)),
            'medianSeconds':statistics.median(row['seconds'] for row in measured) if measured else None,
            'maxSeconds':max((row['seconds'] for row in measured),default=None),
            'processPeakRssBytes':int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*scale,
            'elapsedSeconds':time.monotonic()-begin,'automaticPromotion':False,'nativeLanguageReviewCompleted':False,
            'checkerSha256':{p.name:file_sha(p) for p in [ROOT/'cli/text_quality.py']},
            'limitations':['Seven authored development phrases are not native-reviewed accuracy acceptance.',
                'Only Chinese→Russian is supported; Russian→Chinese and JSON composition are not provided.',
                'Preparation receipt records an earlier conversion process; this inference RSS includes conversion only when preparationReused is false.']}
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({key:result[key] for key in ['model','applicable','measured','checksPassed','medianSeconds','maxSeconds','processPeakRssBytes','elapsedSeconds']},ensure_ascii=False),flush=True)
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir',required=True);parser.add_argument('--archive',required=True)
    parser.add_argument('--cache',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--max-seconds',type=int,default=120);args=parser.parse_args()
    if not 30<=args.max_seconds<=180:parser.error('Use a bounded 30–180 second pilot')
    if args.prepare_only:
        from learning_scheduler import active_imports,model_resource
        if active_imports(Path(args.cache))!=0:raise ValueError('Active or unknown imports: postpone preparation')
        with model_resource(Path(args.cache),kind='foreground',timeout=3):
            receipt=prepare(args)
            print(json.dumps({key:receipt[key] for key in ['model','archiveSha256','convertedSha256','convertedBytes',
                'preparationSeconds','preparationPeakRssBytes']},ensure_ascii=False),flush=True)
    else:
        run(args)
