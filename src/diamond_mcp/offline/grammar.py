"""Compact, deterministic JSON layouts for the two local inference contracts."""
from __future__ import annotations

import json
import re

VERSION='compact-json-1'


def literal(value):
    return json.dumps(value,ensure_ascii=False)


def compile_grammar(schema, refs):
    refs=sorted(set(refs))
    if any(not isinstance(ref,str) or not re.fullmatch(r'E\d{1,12}',ref) for ref in refs):
        raise ValueError('Use generated evidence reference IDs only')
    if len(refs)>64:raise ValueError('Evidence grammar exceeds its reference budget')
    rules=[r'string ::= "\"" char* "\""',r'nonempty ::= "\"" char+ "\""',
           r'char ::= [^"\\\x00-\x1f] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4})',
           'ref ::= '+' | '.join(literal(json.dumps(ref)) for ref in refs or ['UNAVAILABLE']),
           'citations ::= "[" ref ("," ref){0,7} "]"']
    if 'annotations' in schema.get('properties',{}):
        fields=schema['properties']['annotations']['items']['properties']['ref']['enum']
        if not 1<=len(fields)<=32:raise ValueError('Use 1-32 field annotations per generation')
        if len(set(fields))!=len(fields) or not set(fields)<=set(refs):raise ValueError('Field grammar references are outside the evidence package')
        names=[]
        choices=schema['properties']['annotations']['items']['properties']['meaning'].get('enum')
        if choices is not None:
            if not 1<=len(choices)<=32 or any(not isinstance(c,str) or not 1<=len(c)<=120 for c in choices):raise ValueError('Meaning choices exceed their grammar budget')
            rules.append('meaning ::= '+' | '.join(literal(json.dumps(choice)) for choice in choices))
        else:rules.append('meaning ::= string')
        for index,ref in enumerate(fields):
            name='field-'+str(index);names.append(name)
            rules.append(name+' ::= '+literal('{"ref":'+json.dumps(ref)+',"meaning":')+
                         ' meaning '+literal(',"status":')+' status '+literal(',"citations":['+json.dumps(ref))+
                         ' ("," ref){0,7} '+literal(']}'))
        rules += ['status ::= '+literal('"hypothesis"')+' | '+literal('"unresolved"'),
                  'root ::= '+literal('{"annotations":[')+' '+(' '+literal(',')+' ').join(names)+' '+literal(']}')]
    else:
        rules += ['claim ::= '+literal('{"text":')+' nonempty '+literal(',"citations":')+' citations '+literal('}'),
                  'claims ::= "[" (claim ("," claim){0,2})? "]"' if refs else 'claims ::= "[]"',
                  'unknowns ::= "[" (string ("," string){0,2})? "]"',
                  'action ::= '+literal('"search"')+' | '+literal('"context"'),
                  'request ::= '+literal('{"action":')+' action '+literal(',"query":')+' nonempty '+literal('}'),
                  'requests ::= "[" (request ("," request){0,1})? "]"',
                  'root ::= '+literal('{"claims":')+' claims '+literal(',"unknowns":')+' unknowns '+literal(',"requests":')+' requests '+literal('}')]
    return '\n'.join(rules)+'\n'
