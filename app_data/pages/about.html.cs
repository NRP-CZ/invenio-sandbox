<h1 id="sandbox-cesnet-invenio">Sandbox CESNET Invenio</h1>
<p><strong>Sandbox CESNET Invenio</strong> je instancí repozitářového systému <strong>CESNET Invenio</strong>. Sandbox spravují metodici repozitářových systémů NRP.</p>
<p>Samotný systém <a href="https://nrp-cz.github.io/docs/">CESNET Invenio</a> je rozšířením <a href="https://inveniordm.docs.cern.ch/">InvenioRDM</a>, platformy pro správu výzkumných dat založené na <a href="https://inveniosoftware.org/products/framework/">Invenio Frameworku</a> a <a href="https://www.zenodo.org/">Zenodu</a>. Je jedním z hlavních repozitářových systémů <a href="https://www.eosc.cz/projekty/narodni-repozitarova-platforma-pro-vyzkumna-data-nrp/nrp">Národní repozitární platformy (NRP)</a>. Více informací o podmínkách pro vytvoření nové instance systému CESNET Invenio v NRP najdete na stránce služby <a href="https://www.eosc.cz/sluzby/ukladani/repozitare-v-nrp">Repozitáře v NRP</a>.</p>

<h2 id="c-l-sandboxu">Cíl sandboxu</h2>
<p>Sandbox je bezpečné prostředí, kde si kdokoli může vyzkoušet standardní funkce CESNET Invenio:</p>
<ul>
<li>projít si proces ukládání dat a vyplnění metadat v implementaci Czech Core Metadata Modelu (CCMM),</li>
<li>vyzkoušet si API a otestovat ukládací skripty a pipelines,</li>
<li>používat Sandbox v kurzech a na workshopech.</li>
</ul>

<h2 id="zakladni-pravidla">Základní pravidla používání</h2>
<ul>
<li>Kdokoli s platnou identitou e-INFRA.cz se může přihlásit a automaticky získává oprávnění ukládat data jako samostatný vkladatel.</li>
<li>Každý uživatel může vytvořit komunitu.</li>
<li>Repozitář má dvě demo komunity, kde si uživatelé mohou vyzkoušet standardní depoziční workflow.</li>
<li>Repozitář přiřazuje pouze testovací DOI, které nejsou funkční.</li>
<li>Všechna uložená data a metadata jsou pravidelně mazána.</li>
</ul>

<h2 id="technick-pozn-mka">Technická poznámka</h2>
<p>Programový kód Sandboxu CESNET Invenio je forkem kódu datového Catch-all repozitáře a sdílí s ním stejnou architekturu i funkce. Hlavními rozdíly jsou automatické přiřazení práva ukládat data a vytvářet komunity. V ostatních bodech lze odkazovat na uživatelskou dokumentaci datového Catch-all repozitáře (viz níže).</p>

<h2 id="u-ite-n-odkazy">Užitečné odkazy</h2>
<ul>
<li><a href="https://github.com/NRP-CZ/invenio-sandbox">Kód repozitáře na GitHubu</a></li>
<li><a href="https://docs.nrp.eosc.cz/en/docs/end_users/catch-all-data-repository/catch-all-repository-introduction">Uživatelská dokumentace ke Catch-all repozitáři</a></li>
<li><a href="https://nrp-cz.github.io/docs/">Dokumentace CESNET Invenio</a></li>
<li><a href="https://inveniordm.web.cern.ch/">Sandbox InvenioRDM</a></li>
<li><a href="https://inveniordm.docs.cern.ch/">Dokumentace InvenioRDM</a></li>
<li><a href="https://www.eosc.cz/sluzby/ukladani/repozitare-v-nrp">Repozitáře v NRP &ndash; stránka služby</a></li>
<li><a href="https://ccmm.cz">Czech Core Metadata Model (CCMM)</a></li>
</ul>
