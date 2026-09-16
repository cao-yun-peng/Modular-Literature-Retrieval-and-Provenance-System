"""Materialize the newly authored 20-case pilot against frozen source offsets."""

from __future__ import annotations

import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.observability.evaluation.frozen_pilot import (  # noqa: E402
    SCHEMA,
    FrozenCorpus,
    normalize,
    now,
    validate,
    write,
)

BASELINE = Path("data/baselines/papers48-20260916")
OUTPUT = Path("data/paper_benchmark/papers48-pilot20-v1/dataset.json")

# These are source-authored questions, not sampled real-user logs. They were
# written before running retrieval. The source coordinates are not query filters.
QUESTIONS = [
    (
        "zh",
        "mechanism",
        "两种组分都只进行扩散、各自数量守恒的混合物，静止的分相图案能在什么机制下开始整体移动？请找到理论依据。",
        "拮抗的交叉扩散率引入非互易耦合；非互易性超过临界值后，静止分相图案获得有限速度，打破空间与时间反演及静态图案的反射对称性。",
        [
            (
                "00f5",
                1,
                "We demonstrate that nonreciprocity",
                "We elucidate the generic nature",
                "扩散耦合与运动转变",
            )
        ],
    ),
    (
        "en",
        "precise_entity",
        "How does the Kreiss constant help explain transient socio-economic bubbles without tuning an imitation model close to a critical point?",
        "非对称、层级化的非正规网络可在亚临界区间产生短暂且不可持续的羊群行为放大；论文以 Kreiss 常数量化网络非正规性，并将其与金融泡沫大小联系起来。",
        [
            (
                "09df",
                2,
                "We show that our model does not require",
                "Our proposed mechanism presents",
                "非正规性、亚临界放大与 Kreiss 常数",
            )
        ],
    ),
    (
        "zh",
        "method_condition",
        "通过时空变化的活性来操控二维液晶中的单个拓扑缺陷时，需要控制哪些运动自由度？把缺陷作为可控对象的近似前提是什么？",
        "需控制两个平移和一个转动自由度，具体对应缺陷核处的局部流速与涡量；假设向列取向畸变比缺陷动力学更快松弛，从而忽略所述方程的非线性。",
        [
            (
                "13b9",
                4,
                "By assuming a separation of timescales",
                "But the control parameter",
                "时间尺度假设与三个自由度",
            )
        ],
    ),
    (
        "en",
        "method_condition",
        "In a two-dimensional spin model with vision-cone interactions, what ingredient is necessary for true long-range order, and how is directional defect motion detected?",
        "视锥导致的、依赖具体构型的键稀释是必要成分；缺陷定向传播破坏宇称与时间反演对称性，可由非零熵产生率检测。",
        [("1feb", 0, "A necessary ingredient", None, "键稀释与熵产生信号")],
    ),
    (
        "zh",
        "result",
        "高密度神经祖细胞形成向列排列后，正、负半整数拓扑缺陷附近的细胞密度分别怎样变化？是否会出现三维结构？",
        "细胞在 +1/2 缺陷附近快速积累并形成三维丘状结构，在 -1/2 缺陷附近逃离；论文提出各向异性摩擦与活性力场的耦合作为密度不稳定的机制。",
        [
            (
                "292c",
                5,
                "We identified rapid cell accumulation",
                "We used NPC culture",
                "正缺陷积累、负缺陷流出与三维丘状结构",
            )
        ],
    ),
    (
        "en",
        "method_condition",
        "Can orientationally ordered active solids remain stable, and what does sufficiently large active forcing do when momentum is conserved?",
        "线性理论允许二维准长程有序、三维长程有序的稳定活性固体；在动量守恒体系中，足够大的活性驱动会使单轴有序态失稳，与驱动的符号无关。",
        [("31aa", 0, "Our predictions include", None, "稳定有序与动量守恒下的失稳条件")],
    ),
    (
        "zh",
        "mechanism",
        "能否把本身不运动的胶体颗粒拼装成会平移或旋转的单元？寻找利用光来控制这种运动涌现的研究。",
        "可用激光控制两种原本不运动的微球结合；活性在组装后涌现，形成平移、旋转等活性分子行为，控制机制涉及光可调的非互易相互作用。",
        [("3535", 0, "Here, we provide a different route", None, "静止微球组装后涌现活性")],
    ),
    (
        "zh",
        "result",
        "调节什么物理参数可以让活性物质从流体动力学主导过渡到流动被屏蔽？过渡附近观察到了什么涡旋和缺陷结构？",
        "使用摩擦作为控制参数；在湿活性物质与干活性物质的过渡附近，涡旋自组织为晶格，并伴随拓扑缺陷的空间有序，形成类似活性晶体的结构。",
        [
            (
                "4ad0",
                7,
                "Here we demonstrate both theoretically",
                "The emergence of vortex lattices",
                "摩擦控制与涡旋晶格、缺陷有序",
            )
        ],
    ),
    (
        "zh",
        "method_condition",
        "如果粒子只根据视野内同伴改变运动能力、并不主动转向，怎样保持群体凝聚？扩大视野后应怎样调整响应条件？",
        "实验用外部反馈回路控制粒子运动能力；窄视野下可形成非极性的凝聚群体而无需主动转向，视野变宽时可降低响应阈值维持凝聚。",
        [
            (
                "516b",
                2,
                "We tested this principle in a real system",
                "We expect this motility-induced",
                "反馈实验、视野与阈值条件",
            )
        ],
    ),
    (
        "en",
        "result",
        "For microtubule active nematics confined in disks, which observed defect dynamics were reproduced by the theoretical model, and which slow processes were missed?",
        "理论捕捉了一对 +1/2 缺陷的快速绕行，但未捕捉缓慢螺旋形态转变及缺陷对的周期性成核，也未预测弱限域情况下的环形流。",
        [
            (
                "5185",
                1,
                "Comparing experimental data to a theoretical model",
                "The developed confinement methods",
                "理论成功项与未解释现象",
            )
        ],
    ),
    (
        "zh",
        "mechanism",
        "上皮单层中，为什么细胞挤出更容易发生在正半整数缺陷的头部？局部应力测量提供了什么支持？",
        "+1/2 缺陷头部集中强压缩性各向同性应力，而 -1/2 缺陷附近更偏拉伸；将要挤出的细胞在挤出前承受逐渐增强的压缩，支持缺陷造成局部压缩并促进挤出的机制。",
        [
            (
                "5745",
                6,
                "The isotropic stress provides",
                "Notably, the time",
                "缺陷附近应力符号与挤出前的变化",
            )
        ],
    ),
    (
        "zh",
        "mechanism",
        "活性向列液晶中，收缩型与伸展型驱动对一对缺陷的湮灭速度有什么不同影响？初始缺陷湮灭后体系一定会保持无缺陷吗？",
        "文中的模型及数值结果显示收缩型活性加快、伸展型活性减慢成对湮灭；在所展示的活性体系中，湮灭后的流动或密度条带可再失稳，产生新缺陷并进入持续生成与湮灭的混沌动力学，并非必然保持无缺陷。",
        [
            (
                "6a99",
                7,
                "In contractile systems, activity speeds up",
                "Our simple model predicts",
                "收缩与伸展对湮灭的相反影响",
            ),
            (
                "6a99",
                7,
                "Immediately after collision",
                "The dynamics quickly becomes chaotic",
                "湮灭后条带失稳与缺陷再生成",
            ),
        ],
    ),
    (
        "zh",
        "precise_entity",
        "非互易 Cahn–Hilliard（NRCH）描述多组分活性混合物时保留了什么守恒律？是否允许组分相互转化，非平衡性又如何加入？",
        "与动量汇接触（例如基底摩擦），只保留各组分粒子数守恒，不允许组分互相转化；在平衡 Ginzburg–Landau 动力学上加入不能从自由能导出的、仍保持粒子数守恒的流来表达非互易性。",
        [
            (
                "779e",
                10,
                "We consider a system in contact with a momentum sink",
                "The concentrations of the different",
                "动量汇、各物种数守恒和禁止转化",
            ),
            (
                "779e",
                0,
                "The classical Cahn-Hilliard model",
                "The strength of the asymmetry",
                "非自由能导出的守恒流",
            ),
        ],
    ),
    (
        "zh",
        "mechanism",
        "非互易集体系统从静止对齐态转入持续旋转的手性态时，线性扰动的模态会发生什么特殊变化？",
        "文中所述从对齐或反对齐态到手性态的转变，伴随一个阻尼模态和一个 Goldstone 模态在例外点合并。",
        [
            (
                "8762",
                3,
                "The transition between (anti)aligned and chiral phases",
                "In the chiral phase, the growth rates",
                "阻尼模与 Goldstone 模在例外点合并",
            )
        ],
    ),
    (
        "en",
        "method_condition",
        "Under what elastic assumption do four half-charge defects on a spherical nematic favor a tetrahedral arrangement, and why is that geometry favorable?",
        "在平衡、弯曲与展曲弹性模量相等的假设下，四个 +1/2 缺陷位于内接正四面体顶点时自由能最低；同号缺陷相斥，该排布最大化间距并减少液晶畸变。",
        [
            (
                "95d3",
                3,
                "Under the assumption that the bend and splay",
                "The 3D reconstruction",
                "等弹性常数条件与四面体构型",
            )
        ],
    ),
    (
        "zh",
        "result",
        "非互易二维 XY 自旋体系中，缺陷的形状为什么会影响其运动方向？正缺陷的源/汇形态与负缺陷的运动各向异性分别有什么特点？",
        "所研究模型中汇形态是吸引子，源形态是不稳定不动点；负缺陷附近的极化场形成优选运动路径，沿其对称轴移动较容易，垂直该轴移动则需大量自旋翻转而受抑。",
        [
            ("9611", 6, "Sinks are attractors of the dynamics", "For $T", "源与汇形态的稳定性"),
            (
                "9611",
                6,
                "Importantly, the polarized field",
                "As NR interactions reshape defects",
                "负缺陷沿轴与横向运动差异",
            ),
        ],
    ),
    (
        "en",
        "precise_entity",
        "What did Simha and Ramaswamy predict in 2002 about long-wavelength stability of nematic self-propelled-particle suspensions and low-Reynolds-number polar suspensions?",
        "向列有序自驱粒子悬浮液在长波长下总是绝对不稳定；在仅低雷诺数系统（如细菌）可达到的波数区间，极性有序悬浮液出现对流不稳定。",
        [
            (
                "ce94",
                3,
                "(i) Nematic SPP suspensions",
                "(iv) The variance",
                "向列绝对不稳定与极性对流不稳定",
            )
        ],
    ),
    (
        "zh",
        "cross_paper",
        "正半整数拓扑缺陷是否总会让细胞堆积成三维结构？请对比神经祖细胞培养物与上皮单层中报道的局部细胞行为，并分别给出证据。",
        "不能把神经祖细胞中的现象推广为所有细胞体系的共同结果。神经祖细胞在 +1/2 缺陷处快速积累并形成三维丘状结构；上皮单层中，+1/2 缺陷头部的压缩应力与细胞挤出相关。两项研究的体系及局部响应不同。",
        [
            (
                "292c",
                5,
                "We identified rapid cell accumulation",
                "We propose a generic mechanism",
                "神经祖细胞的积累与丘状结构",
            ),
            (
                "5745",
                6,
                "These results matched the observation",
                "There was also a higher probability",
                "上皮细胞在缺陷头部挤出",
            ),
        ],
    ),
    (
        "zh",
        "cross_paper",
        "不直接对齐粒子速度也能形成集体结构吗？请各找一项通过视锥内位置吸引、以及通过视觉反馈改变运动能力实现群体组织的研究，说明二者规则的区别。",
        "一项最小认知 flocking 模型使用无记忆、视锥内短程位置吸引，不含速度对齐，可产生聚集、局部极性队列及宏观向列结构；另一项实验通过外部反馈按视觉感知改变粒子运动能力，窄视野即可形成无需主动转向的非极性凝聚群体。",
        [
            (
                "cc29",
                0,
                "The model consists of active particles",
                "Combining simulations",
                "视锥内位置吸引与涌现图案",
            ),
            (
                "516b",
                2,
                "We found that a mere motility change",
                "For wider fields of view",
                "视觉感知调节运动能力与反馈实验",
            ),
        ],
    ),
    (
        "zh",
        "cross_paper",
        "非互易相互作用导致的持续动力学是否只有行波这一种？请比较守恒二元扩散混合物与非对称 MacArthur 消费者–资源模型中的结果。",
        "守恒扩散混合物中，足够强的非互易交叉扩散使静止分相图案变成运动图案；非对称 MacArthur 消费者–资源模型中，非互易性增加可触发向混沌动力学的相变，其出现受存活物种数与存活资源数之比控制。不能将不同模型的持续动力学都归为单一行波状态。",
        [
            (
                "00f5",
                1,
                "We demonstrate that nonreciprocity",
                "We elucidate the generic nature",
                "守恒混合物的运动图案",
            ),
            (
                "fc4b",
                0,
                "Here, we investigate the effects",
                "We also numerically calculate",
                "生态消费者–资源模型的混沌相变",
            ),
        ],
    ),
]


def main():
    corpus = FrozenCorpus(BASELINE)
    cases = []
    for i, (lang, kind, query, answer, evidence) in enumerate(QUESTIONS, 1):
        groups = []
        for j, (prefix, index, begin, finish, purpose) in enumerate(evidence, 1):
            chunk = next(
                c
                for c in corpus.chunks.values()
                if c["document_id"].startswith("doc_" + prefix) and c["index"] == index
            )
            text = chunk["text"]
            start = text.index(begin)
            end = text.index(finish, start + len(begin)) if finish else len(text)
            # Exclude trailing license/DOI text from abstract evidence.
            if finish is None and "\n\nPublished under" in text[start:end]:
                end = text.index("\n\nPublished under", start)
            end = len(text[:end].rstrip())
            quote = text[start:end]
            pages = corpus.source_pages(chunk["document_id"])
            normalized = normalize(quote)
            source = None
            for page_number, page in enumerate(pages, 1):
                if normalized in page:
                    source = {
                        "physical_page": page_number,
                        "quote": normalized,
                        "alignment": "exact_normalized",
                        "matched_fraction": 1.0,
                    }
                    break
            if source is None:
                # An exact PDF excerpt helps review OCR differences, but is never
                # asserted to validate the entire parsed quote automatically.
                candidates = []
                for page_number, page in enumerate(pages, 1):
                    match = SequenceMatcher(
                        None, normalized, page, autojunk=False
                    ).find_longest_match()
                    candidates.append(
                        (match.size, page_number, page[match.b : match.b + match.size])
                    )
                size, page_number, excerpt = max(candidates)
                if size >= 30:
                    source = {
                        "physical_page": page_number,
                        "quote": excerpt,
                        "alignment": "partial_exact_candidate_requires_review",
                        "matched_fraction": round(size / len(normalized), 3),
                    }
            span = {
                "document_id": chunk["document_id"],
                "chunk_id": chunk["id"],
                "section": chunk["section"],
                "start": start,
                "end": end,
                "quote": quote,
                "pdf_source": source,
            }
            groups.append({"id": f"g{j}", "purpose": purpose, "alternatives": [[span]]})
        cases.append(
            {
                "id": f"P20-{i:02d}",
                "language": lang,
                "query_type": kind,
                "split": "dev",
                "query": query,
                "answerable": True,
                "reference_answer": answer,
                "evidence_groups": groups,
                "review": {
                    "status": "pending",
                    "reviewer": None,
                    "reviewed_at": None,
                    "source_checked": False,
                    "notes": "",
                },
            }
        )
    dataset = {
        "schema_version": SCHEMA,
        "dataset_id": "papers48-pilot20-v1",
        "created_at": now(),
        "baseline": corpus.identity(),
        "status": "draft_pending_human_review",
        "authoring": {
            "method": "assistant_source_grounded_manual_authoring",
            "source": "new frozen MinerU chunks and local original PDFs",
            "retrieval_seen_before_authoring": False,
            "real_user_queries": False,
        },
        "scope": "20 answerable development questions; whole frozen corpus; no source-paper filters; source-group retrieval evaluation",
        "cases": cases,
    }
    print(validate(dataset, corpus, allow_draft=True))
    if OUTPUT.exists():
        raise FileExistsError("Dataset already exists; create a new version instead of overwriting")
    write(OUTPUT, dataset, BASELINE)


if __name__ == "__main__":
    main()
