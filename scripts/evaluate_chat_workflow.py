"""Measure MCP text usability, not host-model answer quality or UI rendering."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from diamond_mcp.engine import Actor,Config
from diamond_mcp.service import DiscoveryService
from diamond_mcp.server import create_server
from diamond_mcp.chat_output import payload


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--state',required=True,type=Path)
    args=parser.parse_args()
    engine=DiscoveryService(Config(args.state))
    actor=Actor('chat-evaluation',frozenset({'bank'}))
    server=create_server(engine,local_actor=actor)
    started=time.perf_counter()
    session=await server.call_tool('get_session_context',{})
    assert payload(session)['data']['projects']==['bank']
    result=await server.call_tool('discover_application',dict(project_id='bank',application_name='Bank of Anthos'))
    assert not result.is_error
    elapsed=time.perf_counter()-started
    text=result.content[0].text
    data=payload(result)['data']
    os.environ['ORIGINSPAN_MCP_OUTPUT']='json'
    try:
        legacy=await server.call_tool('get_complete_discovery',dict(project_id='bank',application_id=data['application_id'],analysis_id=data['analysis_id']))
    finally:
        os.environ.pop('ORIGINSPAN_MCP_OUTPUT',None)
    json_bytes=len(legacy.content[0].text.encode())
    text_bytes=len(text.encode())
    checks={'markdown_default':text.startswith('# Bank of Anthos discovery'),
        'diagram_in_chat':'```mermaid' in text,
        'field_table_and_sources':'| Field |' in text and '### Sources' in text,
        'country_interface_gap':any(f['name']=='country' for f in data['interface_gaps']),
        'source_field_labels':any(f['name']=='birthday' and f.get('source_labels') for f in data['fields']),
        'no_json_unpacking_needed':'ConvertFrom-Json' not in text and not text.startswith('{'),
        'no_blind_documentation_paging':'documentation' not in ' '.join(data['next_actions']),
        'less_text_than_legacy_json':text_bytes<json_bytes,
        'no_clipped_report':'Display budget reached' not in text}
    # Drill down using a real source query reference, never invented SQL.
    q=data['queries'][0]
    bound=await server.call_tool('get_sql_lineage',dict(project_id='bank',application_id=data['application_id'],analysis_id=data['analysis_id'],query_id=q['id'][:12]))
    checks['source_query_lineage']=payload(bound)['data']['provenance']['kind']=='observed_source_query'
    destination=ROOT/'docs/validation-results/chat-upgrade'
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'banking-chat.md').write_text(text,encoding='utf-8')
    report=dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,
        sdk_tool_calls_for_overview=2,sdk_seconds=elapsed,chat_text_bytes=text_bytes,legacy_json_text_bytes=json_bytes,
        text_reduction_percent=round((1-text_bytes/json_bytes)*100,1),application_id=data['application_id'],analysis_id=data['analysis_id'],
        limitations=['In-process MCP protocol results, not Copilot rendering or host-model answer quality',
            'Source now includes HTML metadata; no individual records read',
            'An overview is not exhaustive runtime discovery; detailed topics may need more calls'])
    (destination/'banking-chat.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    raise SystemExit(0 if report['status']=='PASS' else 1)


if __name__=='__main__':
    asyncio.run(main())
