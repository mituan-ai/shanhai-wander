"""Regenerate the project-native, dependency-free SVG artwork for both READMEs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "assets/readme"
FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,PingFang SC,Microsoft YaHei,sans-serif"


def svg(title, desc, body, height):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="title desc">
<title id="title">{title}</title><desc id="desc">{desc}</desc>
<g font-family="{FONT}">{body}</g></svg>'''


def write_assets():
    ROOT.mkdir(parents=True, exist_ok=True)
    for language in ("zh", "en"):
        zh = language == "zh"
        title = "山海漫游" if zh else "Shanhai Wander"
        category = "旅行路线规划与分享" if zh else "A LITTLE LESS PLANNING. A LITTLE MORE TRAVEL."
        subtitle = "SHANHAI WANDER" if zh else "PICK A ROUTE. MAKE IT YOURS."
        lines = ["把风景、住宿和充电，", "排进你的每一天。"] if zh else ["Every stop. Every stay.", "Your whole trip, in one place."]
        labels = ["看风景", "住一晚", "充好电"] if zh else ["Explore", "Stay", "Recharge"]
        body = f'''<rect width="1200" height="350" rx="18" fill="#f4f5ed"/>
<path d="M565 350V230L695 103 776 210 893 57 1036 226 1200 158V350Z" fill="#e2e8da"/>
<path d="M665 350V279L802 174 891 278 1026 152 1200 286V350Z" fill="#c8d8bc"/>
<circle cx="1101" cy="71" r="30" fill="#e7c9a0"/>
<text x="48" y="48" font-size="16" fill="#6e816e" letter-spacing="2">{category}</text>
<text x="46" y="137" font-size="{66 if zh else 58}" font-weight="650" fill="#234f40">{title}</text>
<text x="49" y="176" font-size="19" fill="#6e816e" letter-spacing="3">{subtitle}</text>
<text x="49" y="237" font-size="25" fill="#3c5745">{lines[0]}</text>
<text x="49" y="276" font-size="25" fill="#3c5745">{lines[1]}</text>
<path d="M620 322C637 235 747 301 804 220S924 300 984 231 1084 297 1160 252" fill="none" stroke="#f4f5ed" stroke-width="18"/>
<path d="M620 322C637 235 747 301 804 220S924 300 984 231 1084 297 1160 252" fill="none" stroke="#78966f" stroke-width="2" stroke-dasharray="6 8"/>
'''
        for x, y, label, icon in [(700,258,labels[0],'scenic'),(878,248,labels[1],'stay'),(1070,270,labels[2],'charge')]:
            body += f'<g transform="translate({x} {y})"><circle r="23" fill="#285544"/><text y="-39" text-anchor="middle" fill="#285544" font-size="21" font-weight="600">{label}</text>'
            if icon == 'scenic':
                body += '<path d="m-13 8 9-18 7 12 5-8 7 14Z" fill="none" stroke="#f8f8f3" stroke-width="2" stroke-linejoin="round"/>'
            elif icon == 'stay':
                body += '<path d="m-13-2 13-10 13 10M-9-5V11H9V-5M-3 11V2H3V11" fill="none" stroke="#f8f8f3" stroke-width="2" stroke-linejoin="round"/>'
            else:
                body += '<path d="m3-14-11 16H0L-3 14 10-3H2Z" fill="#f8f8f3"/>'
            body += '</g>'
        (ROOT / f'hero-{language}.svg').write_text(svg(title, 'An itinerary connects sightseeing, an overnight stay and a charging stop; the landscape uses the app’s existing mountain-road motif.', body,350),encoding='utf-8')
        titles = ['选一条路线','安排每一天','保存，出发'] if zh else ['Choose a route','Plan each day','Save and go']
        descriptions = ['30 条经典路线，也能自己创建','景点、酒店、充电停靠一起排','留在账号里，也能导出与分享'] if zh else ['Start with 30 routes, or your own','Add places, stays and charging stops','Keep it private, export or share']
        body='<rect width="1200" height="220" rx="16" fill="#285544"/>'
        for i,(heading,detail) in enumerate(zip(titles,descriptions)):
            x=48+i*396
            body+=f'<text x="{x}" y="62" font-family="Georgia,serif" font-size="31" fill="#c7d6b7">0{i+1}</text><text x="{x}" y="112" font-size="27" font-weight="600" fill="#fff">{heading}</text><text x="{x}" y="157" font-size="18" fill="#d1dfca">{detail}</text>'
            if i < 2:
                body += f'<path d="M{x+322} 70h27m-8-8 8 8-8 8" stroke="#c7d6b7" stroke-width="2" fill="none"/>'
        (ROOT/f'workflow-{language}.svg').write_text(svg('Three steps to your next trip','Choose a route, arrange your stops, then save and share.',body,220),encoding='utf-8')


if __name__ == '__main__':
    write_assets()
