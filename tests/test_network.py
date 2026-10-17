import pytest

from jobseeker import network
from jobseeker.network import Contact, draft_note, parse_connections, rank, role_of, same_company, search_links

EXPORT = """Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Priya,Shah,https://www.linkedin.com/in/priya,,"Acme, Inc.","Senior Software Engineer, Payments Ledger",14 Mar 2022
Marcus,Lee,https://www.linkedin.com/in/marcus,,Acme,"Engineering Manager, Ledger",02 Sep 2024
Dana,Kim,https://www.linkedin.com/in/dana,,Acme Corporation,Technical Recruiter,19 Jan 2026
Tom,Alvarez,https://www.linkedin.com/in/tom,,Acme,Product Designer,30 Jun 2019
Wei,Zhang,https://www.linkedin.com/in/wei,,Acmeco,Staff Engineer,11 Nov 2021
"""


def test_parse_linkedin_export_skips_the_notes_preamble():
    cs = parse_connections(EXPORT)
    assert [c.name for c in cs] == ["Priya Shah", "Marcus Lee", "Dana Kim", "Tom Alvarez", "Wei Zhang"]
    assert cs[0].company == "Acme, Inc." and cs[0].connected_on == "14 Mar 2022"
    with pytest.raises(ValueError, match="Connections.csv"):
        parse_connections("name,company\nx,y\n")


@pytest.mark.parametrize("a, b, same", [
    ("Acme, Inc.", "Acme", True), ("Acme Corporation", "Acme", True), ("The Acme Company", "acme", True),
    ("Capital One Financial", "Capital One", True), ("Acmeco", "Acme", False), ("", "Acme", False),
    ("Amazon Web Services (AWS)", "Amazon Web Services", True),
])
def test_company_matching(a, b, same):
    assert same_company(a, b) is same


@pytest.mark.parametrize("position, role", [
    ("Senior Software Engineer", "engineer"), ("Engineering Manager, Ledger", "hiring manager"),
    ("Director of Engineering", "hiring manager"), ("Technical Recruiter", "recruiter"),
    ("VP of Engineering", "executive"), ("Product Designer", "other"), ("Talent Partner", "recruiter"),
])
def test_roles(position, role):
    assert role_of(position) == role


def test_rank_prefers_same_team_engineers_and_closeness():
    cs = parse_connections(EXPORT)
    leads = rank(cs, "Acme", "Staff Software Engineer, Payments Ledger")
    assert [ld.contact.name for ld in leads] == ["Priya Shah", "Marcus Lee", "Dana Kim", "Tom Alvarez"]  # not Acmeco
    assert leads[0].team_match == ["payments", "ledger"] and leads[0].likelihood == "Medium"
    cs[3].closeness = "worked together"                   # knowing someone well outweighs their role
    top = rank(cs, "Acme", "Staff Software Engineer, Payments Ledger")
    assert top[0].contact.name == "Priya Shah" and top[1].contact.name == "Tom Alvarez"
    cs[0].closeness = "never talked"
    assert rank(cs, "Acme", "Payments Ledger")[0].contact.name == "Tom Alvarez"


def test_import_tags_and_manual_contacts(ws):
    assert network.save_import(ws, EXPORT) == 5
    cs = network.load_contacts(ws)
    network.set_closeness(ws, cs[0], "worked together", note="Globex, same team")
    network.set_closeness(ws, cs[0], "know well")              # updates in place, no duplicate
    with ws.contacts_path.open("a") as f:
        f.write('\n[[contact]]\nname = "Ana Ruiz"\ncompany = "Acme"\nposition = "Staff Engineer, Ledger"\n'
                'closeness = "close"\n')
    cs = network.load_contacts(ws)
    priya = next(c for c in cs if c.name == "Priya Shah")
    assert (priya.closeness, priya.note) == ("know well", "Globex, same team")
    assert ws.contacts_path.read_text().count("Priya Shah") == 1
    ana = next(c for c in cs if c.name == "Ana Ruiz")
    assert ana.source == "manual" and rank(cs, "Acme", "Ledger")[0].contact.name == "Ana Ruiz"
    with pytest.raises(ValueError):
        network.set_closeness(ws, priya, "best friends")
    assert network.counts_by_company(cs, ws.cfg["companies"]) == {"Acme": 5}


def test_search_links_and_notes():
    links = search_links("Acme", "Staff Software Engineer, Payments Ledger", ["Globex"])
    assert "keywords=Acme+engineering+manager+payments+ledger" in links[0]["url"]
    assert links[-1]["label"] == "Ex-Globex at Acme"
    assert "site%3Alinkedin.com%2Fin" in links[3]["url"]
    job = {"company": "Acme", "title": "Staff Software Engineer, Payments Ledger"}
    me = {"current_title": "Senior Software Engineer", "current_company": "Globex"}
    friend = rank([Contact("Priya Shah", "Acme", "Software Engineer", closeness="worked together")], "Acme", "x")[0]
    assert draft_note(friend, job, me, "R-1042").startswith("Hi Priya! I'm applying for Staff Software Engineer, "
                                                            "Payments Ledger (R-1042) at Acme. Would you be comfortable")
    manager = rank([Contact("Marcus Lee", "Acme", "Engineering Manager")], "Acme", "x")[0]
    assert "on your team" in draft_note(manager, job, me)
    for lead in (friend, manager):
        assert len(draft_note(lead, job, me, "R-1042")) <= 300
