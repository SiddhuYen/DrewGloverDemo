from sqlalchemy import select

from app.edges.names import person_norm_key
from app.ingest.seed import seed_direct_connections
from app.models import Person, RelationshipEdge, Source


def test_seed_direct_connections_adds_bryce_as_drews_instagram_mutual(db):
    assert seed_direct_connections(db) == 1
    assert seed_direct_connections(db) == 1

    drew = db.scalar(select(Person).where(
        Person.norm_name == person_norm_key("Drew Glover")))
    bryce = db.scalar(select(Person).where(
        Person.norm_name == person_norm_key("Bryce Johnson")))
    assert bryce.is_warm is True
    assert bryce.meta["instagram_handle"] == "@brycent"

    edge = db.scalar(select(RelationshipEdge).where(
        RelationshipEdge.person_a_id.in_([drew.id, bryce.id]),
        RelationshipEdge.person_b_id.in_([drew.id, bryce.id])))
    assert db.query(RelationshipEdge).filter(
        RelationshipEdge.person_a_id.in_([drew.id, bryce.id]),
        RelationshipEdge.person_b_id.in_([drew.id, bryce.id])).count() == 1
    assert edge.relationship_type == "instagram_mutual"
    assert edge.structural is True
    assert edge.warmth_tier == 1

    source = db.get(Source, edge.source_id)
    assert source.url == "https://www.instagram.com/brycent/"
