import pytest

from app.graph import GraphError, KnowledgeGraph


def graph(tmp_path):
    return KnowledgeGraph(tmp_path / "newsroom.db")


def seed_three(g: KnowledgeGraph):
    g.add_entity(entity_key="nvidia", label="NVIDIA", entity_type="COMPANY")
    g.add_entity(entity_key="supermicro", label="Supermicro", entity_type="COMPANY")
    g.add_entity(entity_key="a-co", label="A기업", entity_type="COMPANY")


def test_directional_verified_two_hop_path(tmp_path):
    g = graph(tmp_path)
    seed_three(g)
    g.add_edge(
        source_key="nvidia",
        target_key="supermicro",
        relationship_type="DIRECT_SUPPLY",
        verification_status="VERIFIED",
        confidence=92,
        evidence="공식 공급 관계 확인",
    )
    g.add_edge(
        source_key="supermicro",
        target_key="a-co",
        relationship_type="DIRECT_SUPPLY",
        verification_status="VERIFIED",
        confidence=87,
        evidence="회사 IR 확인",
    )
    result = g.find_paths(source_key="nvidia", target_key="a-co")
    assert result["status"] == "FOUND"
    assert result["paths"][0]["hops"] == 2
    assert result["paths"][0]["confidence"] == 87.0
    assert result["paths"][0]["verification_status"] == "VERIFIED"
    assert result["paths"][0]["missing_links"] == []


def test_reverse_direction_does_not_invent_path(tmp_path):
    g = graph(tmp_path)
    seed_three(g)
    g.add_edge(
        source_key="nvidia",
        target_key="supermicro",
        relationship_type="DIRECT_SUPPLY",
        verification_status="VERIFIED",
        confidence=90,
        evidence="근거",
    )
    assert g.find_paths(source_key="supermicro", target_key="nvidia")["status"] == "NO_PATH"


def test_inferred_edge_creates_missing_link(tmp_path):
    g = graph(tmp_path)
    seed_three(g)
    g.add_edge(
        source_key="nvidia",
        target_key="supermicro",
        relationship_type="CUSTOMER_OF_CUSTOMER",
        verification_status="INFERRED",
        confidence=61,
        evidence="2차 자료 기반 추정",
    )
    g.add_edge(
        source_key="supermicro",
        target_key="a-co",
        relationship_type="DIRECT_SUPPLY",
        verification_status="VERIFIED",
        confidence=88,
        evidence="회사 확인",
    )
    path = g.find_paths(source_key="nvidia", target_key="a-co")["paths"][0]
    assert path["verification_status"] == "INFERRED"
    assert path["confidence"] == 61.0
    assert len(path["missing_links"]) == 1
    assert "1차 자료" in path["missing_links"][0]["need_to_confirm"]


def test_relationship_and_verification_are_independent(tmp_path):
    g = graph(tmp_path)
    g.add_entity(entity_key="x", label="X", entity_type="COMPANY")
    g.add_entity(entity_key="y", label="Y", entity_type="COMPANY")
    edge = g.add_edge(
        source_key="x",
        target_key="y",
        relationship_type="CAPEX_BENEFICIARY",
        verification_status="PARTIALLY_VERIFIED",
        confidence=73,
        evidence="CAPEX 계획은 확인, 수혜 직접성은 추가 확인 필요",
    )
    assert edge["relationship_type"] == "CAPEX_BENEFICIARY"
    assert edge["verification_status"] == "PARTIALLY_VERIFIED"


def test_invalid_edge_type_is_rejected(tmp_path):
    g = graph(tmp_path)
    g.add_entity(entity_key="x", label="X", entity_type="COMPANY")
    g.add_entity(entity_key="y", label="Y", entity_type="COMPANY")
    with pytest.raises(GraphError):
        g.add_edge(
            source_key="x",
            target_key="y",
            relationship_type="RELATED_STOCK",
            verification_status="VERIFIED",
            confidence=90,
            evidence="근거",
        )
