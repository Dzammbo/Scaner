"""Readable Russian display names for manual LIVE; source provider names stay intact."""
import re
import unicodedata

NAMES = {
    'Türkiye Cup': 'Кубок Турции',
    'Slovenia 2. SNL': 'Словения, 2-я лига',
    'Bulgaria Second League': 'Болгария, Вторая лига',
    'Georgia Cup': 'Кубок Грузии',
    'Bosnia & Herzegovina Cup': 'Кубок Боснии и Герцеговины',
    'Italy Serie A': 'Италия, Серия А',
    'FK Laktasi': 'Лакташи',
    'Zrinjski Mostar': 'Зриньски Мостар',
    'Sassuolo': 'Сассуоло',
    'Lazio': 'Лацио',
    'Portugal Primeira Liga': 'Португалия, Примейра-лига',
    'England Premier League': 'Англия, Премьер-лига',
    'Yeni Mersin Idmanyurdu': 'Ени Мерсин Идманюрду',
    'Mus Spor FC': 'Муш Спор',
    'Tabor Sezana': 'Табор Сежана',
    'ND Slovan Ljubljana': 'Слован Любляна',
    'Ludogorets Razgrad II': 'Лудогорец Разград-2',
    'PFC Dobrudzha Dobrich': 'Добруджа Добрич',
    'FC Gagra': 'Гагра',
    'FC Rustavi': 'Рустави',
}
COUNTRIES = {
    'England':'Англия', 'Scotland':'Шотландия', 'Wales':'Уэльс',
    'Ireland':'Ирландия', 'Spain':'Испания', 'Italy':'Италия',
    'Germany':'Германия', 'France':'Франция', 'Portugal':'Португалия',
    'Netherlands':'Нидерланды', 'Belgium':'Бельгия', 'Turkey':'Турция',
    'Türkiye':'Турция', 'Turkiye':'Турция', 'Slovenia':'Словения',
    'Slovakia':'Словакия', 'Bulgaria':'Болгария', 'Georgia':'Грузия',
    'Greece':'Греция', 'Serbia':'Сербия', 'Croatia':'Хорватия',
    'Poland':'Польша', 'Romania':'Румыния', 'Austria':'Австрия',
    'Switzerland':'Швейцария', 'Denmark':'Дания', 'Sweden':'Швеция',
    'Norway':'Норвегия', 'Finland':'Финляндия', 'USA':'США',
    'United States':'США', 'Brazil':'Бразилия', 'Argentina':'Аргентина',
    'Mexico':'Мексика', 'Japan':'Япония', 'South Korea':'Южная Корея',
}
SPECIAL = str.maketrans({'ı':'и','Ł':'Л','ł':'л','Ø':'О','ø':'о',
                         'Æ':'Ае','æ':'ае','Ð':'Д','ð':'д','Þ':'Т','þ':'т','ß':'сс'})
DIGRAPHS = {'shch':'щ','sch':'щ','zh':'ж','kh':'х','ts':'ц','ch':'ч',
            'sh':'ш','ph':'ф','th':'т','ya':'я','yu':'ю','yo':'ё','ye':'е',
            'ck':'к','qu':'кв'}
LETTERS = dict(zip('abcdefghijklmnopqrstuvwxyz',
                   ['а','б','к','д','е','ф','г','х','и','дж','к','л','м',
                    'н','о','п','к','р','с','т','у','в','в','кс','й','з']))


def _word(match):
    original=match.group()
    if original in ('FC','PFC','AFC','SC','CF','ND'):
        return 'ФК' if original in ('FC','PFC','AFC','CF') else ('СК' if original=='SC' else 'НД')
    if original in ('II','III','IV'):
        return {'II':'2','III':'3','IV':'4'}[original]
    word=original.lower();result=[];i=0
    while i<len(word):
        found=next((key for key in DIGRAPHS if word.startswith(key,i)),None)
        if found:result.append(DIGRAPHS[found]);i+=len(found)
        else:result.append(LETTERS.get(word[i],word[i]));i+=1
    answer=''.join(result)
    return answer[:1].upper()+answer[1:] if original[:1].isupper() else answer


def russian_name(value, league=False):
    source=str(value or '').strip()
    if source in NAMES:return NAMES[source]
    if league:
        for country,ru in sorted(COUNTRIES.items(),key=lambda pair:-len(pair[0])):
            if source==country:return ru
            if source.startswith(country+' '):
                rest=source[len(country)+1:]
                if rest=='Cup':return 'Кубок '+({'Турция':'Турции','Грузия':'Грузии',
                    'Англия':'Англии','Испания':'Испании','Италия':'Италии',
                    'Германия':'Германии','Франция':'Франции'}.get(ru,ru))
                if rest in ('Premier League','First League','Second League',
                            'National League','Premier Division'):
                    return ru+', '+{'Premier League':'Премьер-лига',
                        'First League':'Первая лига','Second League':'Вторая лига',
                        'National League':'Национальная лига',
                        'Premier Division':'Высший дивизион'}[rest]
                return ru+', '+russian_name(rest)
    source=unicodedata.normalize('NFKD',source.translate(SPECIAL))
    source=''.join(c for c in source if not unicodedata.combining(c))
    return re.sub(r'[A-Za-z]+',_word,source)
