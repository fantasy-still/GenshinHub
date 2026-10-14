"""Check that citations open exact frozen files/lines; this is not fact checking."""
import re
from urllib.parse import unquote
from markdown_it import MarkdownIt

def audit(text,knowledge):
    targets=[]
    for block in MarkdownIt('commonmark').parse(text):
        for token in block.children or []:
            href=token.attrGet('href') if token.type=='link_open' else None
            if href and href.startswith('kb://') and href not in targets:targets.append(href)
    checked=[]
    for href in targets:
        path,_,fragment=href[5:].partition('#')
        try:
            if not re.fullmatch(r'L[1-9]\d*',fragment):raise ValueError('缺少有效行号')
            line=int(fragment[1:]);path=unquote(path)
            result=knowledge.read(path,line,1)
            if not result['lines']:raise ValueError('行号超出原文范围')
            checked.append({'href':href,'path':result.get('path',path),'line':line,'valid':True})
        except (ValueError,KeyError,OSError):
            checked.append({'href':href,'valid':False})
    return checked
