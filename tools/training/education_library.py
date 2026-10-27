"""Explicit academic reading cards for the existing cited-knowledge retriever.

Cards contain attributed summaries and analytics learning objectives, not copied
lectures, clinical answer keys or approved patient-specific treatment rules.
"""
import argparse
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlparse
from clinical_core.contracts import digest
from clinical_core.agere.clinical import component_learning as C,vbc_knowledge as K

VERSION='academic-reading-cards/1'
USE_MODES=('cc_by_text_summary','public_reference_summary','link_metadata_only')
PRIMARY_HOSTS=('hbs.edu','harvard.edu','pressbooks.pub','uctv.tv','nih.gov','hhs.gov',
    'emory.edu','mayo.edu','cuanschutz.edu','texasheart.org','houstonmethodist.org',
    'partners.org','bannerhealth.com','arizona.edu','uw.edu','acc.org')


def primary_url(value):
    url=urlparse(value);host=url.hostname or ''
    return url.scheme=='https' and not url.username and not url.password and any(host==h or host.endswith('.'+h) for h in PRIMARY_HOSTS)


def validate(cards):
    if not isinstance(cards,list) or not cards:raise ValueError('An explicit nonempty reading library is required')
    seen=set()
    for card in cards:
        problems=K.entry_problems(card)
        if problems:raise ValueError('Invalid cited academic card: '+str(problems))
        if card['id'] in seen:raise ValueError('Duplicate academic card')
        seen.add(card['id']);scope=card.get('education')
        if not isinstance(scope,dict) or scope.get('use_mode') not in USE_MODES:raise ValueError('Explicit source reuse mode required')
        if scope.get('clinical_rule_approved') is not False or scope.get('copied_full_text') is not False or scope.get('video_transcript_ingested') is not False:
            raise ValueError('Reading cards must not impersonate treatment approval, full text or watched transcripts')
        if 'learning' in card:raise ValueError('Academic background cannot impersonate an approved operational fact lesson')
        if not scope.get('analytics_objectives') or not scope.get('topics'):raise ValueError('Reading needs topics and concrete analytics learning objectives')
        lecture=scope.get('lecture')
        for citation in card['citations']:
            # A hospital's own video page is also primary metadata. Retain the
            # observed publisher identity; YouTube search results alone are not.
            url=urlparse(citation['url']);host=url.hostname or ''
            publisher_video=isinstance(lecture,dict) and url.scheme=='https' and not url.username and not url.password and citation['url']==lecture.get('recording_url') and host in ('www.youtube.com','youtube.com','youtu.be') and bool(lecture.get('publisher_channel')) and bool(lecture.get('publisher_identity_evidence'))
            if not primary_url(citation['url']) and not publisher_video:
                raise ValueError('Use an explicit primary academic or government source')
        if scope['use_mode']=='cc_by_text_summary' and (scope.get('license')!='CC-BY-4.0' or not scope.get('license_evidence_url')):
            raise ValueError('Open text reuse needs per-source license evidence')
        if scope['use_mode']=='cc_by_text_summary' and not primary_url(scope['license_evidence_url']):
            raise ValueError('License evidence must come from the primary source')
        if lecture is not None:
            if scope['use_mode']!='link_metadata_only' or not isinstance(lecture,dict):raise ValueError('Unreviewed lecture content must remain explicitly metadata only')
            if not lecture.get('presenter') or not primary_url(lecture.get('institution_url','')):raise ValueError('Lecture presenter and primary institutional provenance are required')
            if lecture.get('content_review') not in ('metadata_only','description_and_chapters_only'):raise ValueError('Lecture metadata cannot claim full content review')
            if lecture.get('full_lecture_watched') is not False or lecture.get('transcript_status') not in ('not_read','button_opened_load_stalled'):
                raise ValueError('Metadata-only intake cannot claim a watched lecture or reviewed transcript')
            if lecture.get('publisher_channel'):
                channel=urlparse(lecture['publisher_channel'])
                if channel.scheme!='https' or channel.hostname not in ('www.youtube.com','youtube.com') or not channel.path.startswith(('/@','/channel/')) or channel.username or channel.password:
                    raise ValueError('Use the observed publisher channel identity')
            if lecture.get('access') not in ('public_player_verified','publisher_recording_link','unverified_or_restricted'):raise ValueError('Recording availability must be explicit')
            if lecture.get('access')=='public_player_verified' and not lecture.get('access_evidence'):raise ValueError('Public player access needs an observed receipt')
            recording=urlparse(lecture.get('recording_url',''));host=recording.hostname or ''
            if recording.scheme!='https' or recording.username or recording.password or not (primary_url(lecture.get('recording_url','')) or host in ('www.youtube.com','youtube.com','youtu.be','player.vimeo.com') or host.endswith('.hosted.panopto.com')):
                raise ValueError('Use a publisher-linked recording URL')
            if not lecture.get('recording_link_evidence_url') or lecture['recording_link_evidence_url'] not in [c['url'] for c in card['citations']]:raise ValueError('Recording link must retain its primary citation')
            if date.fromisoformat(lecture['event_date'])>date.fromisoformat(card['last_verified']):raise ValueError('A future event is not an available recorded lecture')
    return cards


def build(manifest,output):
    manifest=C.checked_path(manifest);spec=json.loads(manifest.read_text())
    if spec.get('schema')!=VERSION or not isinstance(spec.get('cards'),list):raise ValueError('Explicit academic-reading-cards/1 manifest required')
    cards=validate(spec['cards']);output=C.checked_path(output);output.mkdir(parents=True,exist_ok=False)
    data=output/'academic-reading.jsonl';data.write_text(''.join(json.dumps(c)+'\n' for c in cards))
    # Exercise the existing validator/loader; no new retrieval service or model.
    loaded=K.load(output)
    receipt={'schema':VERSION,'entries':len(loaded),'topics':sorted({topic for c in cards for topic in c['education']['topics']}),
        'input':str(manifest),'manifest_identity':digest(spec),'corpus_digest':K.corpus_digest(output),
        'default_knowledge_directory_changed':False,'provider_calls':0,'model_weights_trained_on_cards':False,'clinical_qualification':False,
        'use':'Existing vbc_knowledge.answer(..., directory=explicit_library) supports cited offline retrieval; clinical rules need separate reviewed evidence and applicability.'}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--manifest',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();print(json.dumps(build(args.manifest,args.output),indent=2))


if __name__=='__main__':main()
