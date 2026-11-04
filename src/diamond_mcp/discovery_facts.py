"""Static framework facts. Never import repositories or include record literals."""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
import yaml

VERSION = 'originspan-discovery-1'


def yaml_documents(source):
    """Use the safe loader and bound document count, including empty documents."""
    result = []
    for index, document in enumerate(yaml.safe_load_all(source)):
        if index >= 1000:
            raise ValueError('Deployment document limit exceeded')
        if isinstance(document, dict):
            result.append(document)
    return result


def extract_facts(source, file):
    facts = []
    suffix = Path(file).suffix.lower()
    def add(kind, name, line, **data):
        if len(facts) >= 12000:
            return
        facts.append(dict(kind=kind, name=name[:300], file=file, line=max(1,line),
                          status='STATIC_EVIDENCE', extractor=VERSION, **data))
    if suffix == '.py':
        try:
            tree = ast.parse(source)
        except (SyntaxError, RecursionError):
            return facts
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        def context(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node.name
            return None
        def code(node):
            # Identifier structure only: literal record/config values are excluded.
            if node is None:
                return ''
            if isinstance(node, ast.Constant):
                return '?'
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Attribute):
                return code(node.value)+'.'+node.attr
            if isinstance(node, ast.Call):
                return code(node.func)+'('+', '.join(code(a) for a in node.args[:5])+')'
            if isinstance(node, ast.Subscript):
                key=node.slice.value if isinstance(node.slice,ast.Constant) and isinstance(node.slice.value,str) and re.fullmatch(r'[\w.-]{1,160}',node.slice.value) else None
                return code(node.value)+('.'+key if key else '[key]')
            if isinstance(node, ast.Compare):
                ops = {'Eq':'=', 'NotEq':'!=','Gt':'>','GtE':'>=','Lt':'<','LtE':'<='}
                return code(node.left)+' '+ ' '.join(ops.get(type(op).__name__,'?')+' '+code(value) for op,value in zip(node.ops,node.comparators))
            if isinstance(node, ast.BinOp):
                return code(node.left)+' '+{'Mult':'*','Div':'/','Add':'+','Sub':'-'}.get(type(node.op).__name__,'?')+' '+code(node.right)
            return type(node).__name__
        def text(node):
            return node.value if isinstance(node,ast.Constant) and isinstance(node.value,str) else None
        def clean_identifier(value):
            return isinstance(value,str) and re.fullmatch(r'[\w.-]{1,160}',value) is not None
        def target(node):
            return code(node.targets[0] if isinstance(node,ast.Assign) else node.target)
        def text_join(node):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='format':
                return isinstance(node.func.value,ast.Constant) and isinstance(node.func.value.value,str)
            return any(isinstance(child,ast.BinOp) and isinstance(child.op,ast.Add) and
                any(isinstance(operand,ast.Constant) and isinstance(operand.value,str) for operand in (child.left,child.right))
                for child in ast.walk(node))
        for node in ast.walk(tree):
            if isinstance(node,(ast.Assign,ast.AnnAssign)) and node.value is not None and not isinstance(node.value,ast.Dict):
                inputs=sorted({text(child.slice) for child in ast.walk(node.value)
                    if isinstance(child,ast.Subscript) and clean_identifier(text(child.slice))})
                if inputs:
                    concatenate=text_join(node.value)
                    add('field_assignment',target(node),node.lineno,output=target(node),inputs=inputs,function=context(node),
                        operation='concatenation' if concatenate else 'source_assignment',flow=', '.join(inputs)+' → '+target(node))
            if isinstance(node,(ast.Assign,ast.AnnAssign)) and isinstance(node.value,ast.Call):
                value=node.value
                callee=code(value.func)
                if callee.split('.')[-1]=='Table' and value.args and clean_identifier(text(value.args[0])):
                    add('orm_table',target(node),node.lineno,table=text(value.args[0]),framework='sqlalchemy')
                    for argument in value.args[1:]:
                        if isinstance(argument,ast.Call) and code(argument.func).split('.')[-1]=='Column' and argument.args and clean_identifier(text(argument.args[0])):
                            add('orm_column',text(argument.args[0]),argument.lineno,table=text(value.args[0]),
                                binding=target(node),data_type=code(argument.args[1]) if len(argument.args)>1 else 'UNKNOWN',framework='sqlalchemy')
            if isinstance(node,ast.Call):
                name=code(node.func)
                if name.endswith(('.getenv','.get')) and node.args:
                    key=text(node.args[0])
                    receiver=code(node.func.value) if isinstance(node.func,ast.Attribute) else ''
                    if clean_identifier(key) and ('environ' in receiver or name.endswith('.getenv')):
                        add('environment_reference',key,node.lineno,function=context(node))
                if isinstance(node.func,ast.Attribute) and node.func.attr in ('select','insert','update','delete'):
                    receiver=code(node.func.value)
                    if receiver and not receiver.endswith('session'):
                        ancestor=node
                        while parents.get(ancestor) is not None and not isinstance(parents[ancestor],(ast.Assign,ast.AnnAssign,ast.Expr,ast.Return)):
                            ancestor=parents[ancestor]
                        predicates=[code(n.args[0]) for n in ast.walk(ancestor) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('where','filter') and n.args]
                        add('orm_operation',node.func.attr,node.lineno,binding=receiver,framework='sqlalchemy_candidate',
                            predicates=predicates,function=context(node))
                operation=None
                if re.search(r'(?:bcrypt\.|\b)(?:hashpw|gensalt|checkpw)$',name):
                    operation='password_hashing' if not name.endswith('checkpw') else 'password_verification'
                elif name.endswith(('jwt.encode','jwt.decode')):
                    operation='token_construction' if name.endswith('encode') else 'token_verification'
                elif name.endswith('bleach.clean'):
                    operation='input_sanitization'
                if operation:
                    add('transformation',operation,node.lineno,operation=name,inputs=[code(a) for a in node.args[:4]],function=context(node))
            if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Mult,ast.Div)):
                constant=next((v.value for v in (node.left,node.right) if isinstance(v,ast.Constant) and type(v.value) in (int,float)),None)
                if constant in (10,100,1000):
                    add('transformation','numeric_scaling',node.lineno,operation=type(node.op).__name__,factor=constant,
                        inputs=[code(node.left),code(node.right)],function=context(node),meaning='Scaling observed; units require surrounding evidence')
            field=None
            if isinstance(node,ast.Subscript) and clean_identifier(text(node.slice)):
                field=text(node.slice)
            elif isinstance(node,ast.Attribute) and isinstance(node.value,ast.Attribute) and node.value.attr=='c':
                field=node.attr
            elif isinstance(node,ast.Dict):
                for key,value in zip(node.keys,node.values):
                    if clean_identifier(text(key)):
                        add('field_usage',text(key),key.lineno,function=context(node),role='dictionary_key')
                        inputs=sorted({text(child.slice) for child in ast.walk(value)
                            if isinstance(child,ast.Subscript) and clean_identifier(text(child.slice))})
                        if not inputs and isinstance(value,ast.Name):
                            inputs=[value.id]
                        if inputs:
                            add('field_assignment',text(key),key.lineno,output=text(key),inputs=inputs,
                                function=context(node),operation='concatenation' if text_join(value) else 'dictionary_assignment',
                                flow=', '.join(inputs)+' → '+text(key))
            if field:
                role='write' if isinstance(node.ctx,ast.Store) else 'read'
                add('field_usage',field,node.lineno,function=context(node),role=role)
                if isinstance(node,ast.Subscript) and isinstance(node.ctx,ast.Store):
                    parent=parents.get(node)
                    if isinstance(parent,ast.Assign) and isinstance(parent.value,ast.Call) and isinstance(parent.value.func,ast.Attribute) and parent.value.func.attr=='get' and code(parent.value.func.value) not in ('os.environ','environ') and not code(node.value).endswith('.config'):
                        add('transformation','lookup_enrichment',node.lineno,output=field,lookup=code(parent.value.func.value),
                            inputs=[code(a) for a in parent.value.args],function=context(node))
    elif suffix in ('.java','.kt'):
        # Comments are not executable declarations. Retain offsets and annotation strings.
        source=re.sub(r'("(?:\\.|[^"\\])*")|(/\*[\s\S]*?\*/|//[^\n]*)',
            lambda match:match[1] if match[1] else re.sub(r'[^\n]',' ',match[0]),source)
        entity=re.search(r'@Entity(?:\s*\([^)]*\))?[\s\S]{0,1000}?\bclass\s+(\w+)',source)
        table=re.search(r'@Table\s*\([^)]*\bname\s*=\s*"([\w.]+)"',source)
        if entity and table:
            add('orm_entity',entity[1],source.count('\n',0,entity.start())+1,table=table[1],framework='jpa')
            pattern=r'@Column\s*\([^)]*\bname\s*=\s*"([\w.]+)"[^)]*\)(?:\s*@\w+(?:\([^)]*\))?)*\s*(?:(?:private|public|protected|final|static)\s+)*[\w<>?\[\]]+\s+(\w+)'
            for match in re.finditer(pattern,source):
                add('orm_column',match[2],source.count('\n',0,match.start())+1,table=table[1],entity=entity[1],column=match[1],framework='jpa')
    elif suffix in ('.html','.htm'):
        from html.parser import HTMLParser
        from urllib.parse import urlsplit
        class Forms(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.form='';self.label=None;self.text=[];self.labels={};self.fields=[]
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if tag=='form':
                    self.form=urlsplit(attrs.get('action','')).path[:200]
                if tag=='label':
                    self.label=attrs.get('for');self.text=[]
                if tag in ('input','select','textarea'):
                    name=attrs.get('name','')
                    if re.fullmatch(r'[\w.-]{1,160}',name):
                        self.fields.append(dict(name=name,identifier=attrs.get('id',''),line=self.getpos()[0],
                            form=self.form,input_type=attrs.get('type',tag),required='required' in attrs))
            def handle_data(self,data):
                if self.label is not None:
                    self.text.append(data)
            def handle_endtag(self,tag):
                if tag=='label':
                    if self.label:
                        self.labels[self.label]=' '.join(' '.join(self.text).split())[:160]
                    self.label=None;self.text=[]
                if tag=='form':
                    self.form=''
        parser=Forms();parser.feed(source)
        for field in parser.fields:
            identifier=field.pop('identifier')
            label=parser.labels.get(identifier,'')
            binding='for_id'
            if not label:
                label=parser.labels.get(field['name'],'')
                binding='for_name_candidate'
            add('form_field',field.pop('name'),field.pop('line'),label=label,label_binding=binding,**field)
    bindings={f['name']:f['table'] for f in facts if f['kind']=='orm_table'}
    for fact in facts:
        if fact['kind']=='orm_operation' and fact['binding'] in bindings:
            fact.update(table=bindings[fact['binding']],framework='sqlalchemy',status='EXPLICIT_TABLE_BINDING')
    return facts


def deployment_facts(source,file):
    """Keep topology metadata only, never config secret bodies or arbitrary values."""
    facts=[]
    lines=source.splitlines()
    def line_for(value):
        return next((i+1 for i,line in enumerate(lines) if str(value) in line),1)
    for document in yaml_documents(source):
        kind=document.get('kind')
        name=document.get('metadata',{}).get('name')
        if not isinstance(name,str):
            continue
        if kind=='ConfigMap':
            for key,value in (document.get('data') or {}).items():
                if not isinstance(value,str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*(?:ADDR|HOST|URL|URI)',str(key)):
                    continue
                match=re.fullmatch(r'(?:https?://)?([a-zA-Z][\w.-]*)(?::\d+)?(?:/[\w/.-]*)?',value.strip())
                if match:
                    facts.append(dict(kind='configured_address',name=key,target=match[1],config=name,file=file,line=line_for(key),status='DECLARED_CONFIG'))
        if kind in ('Deployment','StatefulSet','DaemonSet','Job'):
            spec=document.get('spec',{}).get('template',{}).get('spec',{})
            for container in spec.get('containers',[]):
                for ref in container.get('envFrom',[]):
                    config=ref.get('configMapRef',{}).get('name')
                    if config:
                        facts.append(dict(kind='config_consumer',name=name,config=config,file=file,line=line_for(config),status='DECLARED_CONFIG'))
                for env in container.get('env',[]):
                    ref=env.get('valueFrom',{}).get('configMapKeyRef',{})
                    if ref.get('name') and ref.get('key'):
                        facts.append(dict(kind='config_consumer',name=name,config=ref['name'],key=ref['key'],file=file,line=line_for(env.get('name','')),status='DECLARED_CONFIG'))
        if kind=='Service':
            facts.append(dict(kind='service_declaration',name=name,file=file,line=line_for(name),status='DECLARED_CONFIG'))
    return facts


def evidence_for_facts(facts):
    items=[]
    for fact in facts:
        if fact['kind'] in ('configured_address','config_consumer','service_declaration'):
            continue
        identity=json_identity(fact)
        description='; '.join(str(k)+'='+str(v) for k,v in fact.items() if k not in ('file','line','status','extractor'))
        items.append(dict(id='fact-'+hashlib.sha256(identity.encode()).hexdigest()[:24], name=fact['name'],
            kind=fact['kind'],file=fact['file'],line=fact['line'],end_line=fact['line'],
            description=description[:1800],declaration='observed_fact',extractor=VERSION,extractor_version='1',
            search_text=description,entity=fact.get('table',''),
            **{key:fact[key] for key in ('function','role') if key in fact}))
    return items


def json_identity(fact):
    import json
    return json.dumps(fact,sort_keys=True)
