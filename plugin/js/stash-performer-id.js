// Stash Performer ID — Step 2 image tagger page (UI-only plugin, plain JS over window.PluginApi).
//
// Lists images with the same filter/sort/pagination surface as the native grid (tags / path /
// organized), and per image: Scrape (via our metadata-provider scraper) -> pick or create a
// performer (PerformerSelect, inline create) -> Save (imageUpdate performer_ids).
//
// Shell (filter modal / sort / per-page / pager) adapted from stash-auto-vision-tagging's
// tag-manager.js; the per-image Scrape->Select->Save row is the net-new piece.
// See docs/IMAGE_TAGGER_FEASIBILITY.md.
(function () {
  "use strict";

  // =========================================================================
  // Constants
  // =========================================================================

  var ROUTE = "/plugins/stash-performer-id";
  var STORAGE_PREFIX = "spid.tagger.";
  // Must match the installed scraper yml filename stem (scraper/stash-performer-id.yml).
  var SCRAPER_ID = "stash-performer-id";
  // stash_ids endpoint for the durable name-record link (DESIGN §8). Stable identifier so
  // re-scrapes resolve to the same performer.
  var ENDPOINT = "stash-performer-id";

  var SORT_OPTIONS = [
    { value: "path", label: "Path" },
    { value: "title", label: "Title" },
    { value: "date", label: "Date" },
    { value: "created_at", label: "Created At" },
    { value: "updated_at", label: "Updated At" },
    { value: "random", label: "Random" },
  ];
  var PER_PAGE_OPTIONS = [20, 40, 60, 120, 250];

  // =========================================================================
  // API references
  // =========================================================================

  var api = window.PluginApi;
  var React = api.React;
  var el = React.createElement;
  var GQL = api.GQL;

  var useState = React.useState;
  var useMemo = React.useMemo;
  var useCallback = React.useCallback;
  var useRef = React.useRef;

  var Bootstrap = api.libraries.Bootstrap;
  var Button = Bootstrap.Button;
  var Badge = Bootstrap.Badge;
  var Modal = Bootstrap.Modal;
  var Form = Bootstrap.Form;
  var Dropdown = Bootstrap.Dropdown;
  var Spinner = Bootstrap.Spinner;
  var Accordion = Bootstrap.Accordion;
  var Card = Bootstrap.Card;

  var NavLink = api.libraries.ReactRouterDOM.NavLink;
  var Link = api.libraries.ReactRouterDOM.Link;
  var FA = api.libraries.FontAwesomeSolid;

  // =========================================================================
  // Imperative GraphQL (scrape / resolve / create / associate) via csLib
  // =========================================================================

  function gql(query, variables) {
    // csLib.callGQL resolves to response.data (undefined on GraphQL error).
    return window.csLib.callGQL({ query: query, variables: variables });
  }

  // -------------------------------------------------------------------------
  // Stash version -> which performer stash_ids filter key to use.
  // The criterion was named `stash_id_endpoint` through v0.30.1 and renamed to the plural
  // `stash_ids_endpoint` afterwards. Query the version once (cached) and pick the key so the
  // identify path keeps working across the rename (docs/IMAGE_TAGGER_FEASIBILITY.md §v0.30.1 note).
  // -------------------------------------------------------------------------

  // "v0.30.1" -> [0,30,1]; anything unparseable -> [0,0,0].
  function parseVersion(v) {
    var m = /(\d+)\.(\d+)\.(\d+)/.exec(String(v || ""));
    return m ? [+m[1], +m[2], +m[3]] : [0, 0, 0];
  }

  function versionGt(a, b) {
    for (var i = 0; i < 3; i++) {
      if (a[i] !== b[i]) return a[i] > b[i];
    }
    return false;
  }

  var _stashIdKey = null; // cached Promise<string>
  function stashIdEndpointKey() {
    if (!_stashIdKey) {
      _stashIdKey = gql("{ version { version } }").then(function (d) {
        var v = parseVersion(d && d.version && d.version.version);
        // Post-0.30.1 uses the plural key; 0.30.1 and earlier (and an unreadable version,
        // which falls back to [0,0,0]) use the singular — our documented target.
        return versionGt(v, [0, 30, 1]) ? "stash_ids_endpoint" : "stash_id_endpoint";
      });
    }
    return _stashIdKey;
  }

  // The full ScrapedPerformer selection (all standalone fields our provider may return).
  // career_start/career_end are not queryable on ScrapedPerformer (only the deprecated
  // career_length is exposed), so they are intentionally omitted.
  var _SCRAPED_PERFORMER =
    " performers { name disambiguation aliases gender birthdate death_date ethnicity country" +
    " hair_color eye_color height weight measurements fake_tits penis_length circumcised" +
    " tattoos piercings details urls images stored_id remote_site_id }";

  function scrapeImage(imageId) {
    return gql(
      "query ($source: ScraperSourceInput!, $input: ScrapeSingleImageInput!) {" +
        " scrapeSingleImage(source: $source, input: $input) {" +
        _SCRAPED_PERFORMER +
        " } }",
      { source: { scraper_id: SCRAPER_ID }, input: { image_id: String(imageId) } }
    ).then(function (d) {
      var results = d && d.scrapeSingleImage;
      if (!results || !results.length) return null;
      var perfs = results[0].performers || [];
      return perfs.length ? perfs[0] : null;
    });
  }

  function findPerformers(filter) {
    return gql(
      "query ($f: PerformerFilterType) { findPerformers(performer_filter: $f, filter:" +
        " {per_page: 1}) { performers { id name disambiguation } } }",
      { f: filter }
    ).then(function (d) {
      var list = d && d.findPerformers && d.findPerformers.performers;
      return list && list.length ? list[0] : null;
    });
  }

  function findPerformer(id) {
    return gql("query ($id: ID!) { findPerformer(id: $id) { id name disambiguation } }", {
      id: String(id),
    }).then(function (d) {
      return (d && d.findPerformer) || null;
    });
  }

  // Resolve a scraped performer to an existing local one: stored_id -> stash_id -> name.
  function resolveScraped(scraped) {
    if (scraped.stored_id) {
      return findPerformer(scraped.stored_id);
    }
    var byStashId = scraped.remote_site_id
      ? stashIdEndpointKey().then(function (key) {
          var filter = {};
          filter[key] = {
            endpoint: ENDPOINT,
            stash_id: String(scraped.remote_site_id),
            modifier: "EQUALS",
          };
          return findPerformers(filter);
        })
      : Promise.resolve(null);
    return byStashId.then(function (p) {
      if (p) return p;
      if (!scraped.name) return null;
      return findPerformers({ name: { value: scraped.name, modifier: "EQUALS" } });
    });
  }

  // Source gender strings -> Stash GenderEnum.
  var GENDER_ENUM = {
    female: "FEMALE",
    male: "MALE",
    "transgender female": "TRANSGENDER_FEMALE",
    "trans female": "TRANSGENDER_FEMALE",
    "transgender male": "TRANSGENDER_MALE",
    "trans male": "TRANSGENDER_MALE",
    intersex: "INTERSEX",
    "non-binary": "NON_BINARY",
    "non binary": "NON_BINARY",
  };

  // Build a PerformerCreateInput from an enriched ScrapedPerformer + chosen image (mirrors Stash's
  // PerformerModal.onSaveClicked). `excluded` is a {field: true} map of fields to drop.
  function buildPerformerInput(s, excluded, imageUrl) {
    excluded = excluded || {};
    var input = { name: s.name };
    function put(field, key, val) {
      if (!excluded[field] && val != null && val !== "") input[key] = val;
    }
    put("disambiguation", "disambiguation", s.disambiguation);
    if (!excluded.aliases && s.aliases) {
      input.alias_list = s.aliases
        .split(",")
        .map(function (a) {
          return a.trim();
        })
        .filter(Boolean);
    }
    if (!excluded.gender && s.gender) {
      var g = GENDER_ENUM[String(s.gender).toLowerCase().trim()];
      if (g) input.gender = g;
    }
    put("birthdate", "birthdate", s.birthdate);
    put("death_date", "death_date", s.death_date);
    put("ethnicity", "ethnicity", s.ethnicity);
    put("country", "country", s.country);
    put("hair_color", "hair_color", s.hair_color);
    put("eye_color", "eye_color", s.eye_color);
    if (!excluded.height && s.height) {
      var h = parseInt(s.height, 10);
      if (!isNaN(h)) input.height_cm = h;
    }
    if (!excluded.weight && s.weight) {
      var w = parseInt(s.weight, 10);
      if (!isNaN(w)) input.weight = w;
    }
    put("measurements", "measurements", s.measurements);
    put("fake_tits", "fake_tits", s.fake_tits);
    put("circumcised", "circumcised", s.circumcised);
    put("career_start", "career_start", s.career_start);
    put("career_end", "career_end", s.career_end);
    put("tattoos", "tattoos", s.tattoos);
    put("piercings", "piercings", s.piercings);
    put("details", "details", s.details);
    if (!excluded.urls && s.urls && s.urls.length) input.urls = s.urls;
    if (!excluded.image && imageUrl) input.image = imageUrl;
    if (s.remote_site_id) {
      input.stash_ids = [{ endpoint: ENDPOINT, stash_id: String(s.remote_site_id) }];
    }
    return input;
  }

  function createPerformer(scraped, excluded, imageUrl) {
    var img = imageUrl != null ? imageUrl : scraped.images && scraped.images[0];
    return gql(
      "mutation ($input: PerformerCreateInput!) { performerCreate(input: $input) {" +
        " id name disambiguation } }",
      { input: buildPerformerInput(scraped, excluded, img) }
    ).then(function (d) {
      return (d && d.performerCreate) || null;
    });
  }

  // Ensure an existing performer carries our name-record stash_id, so future scrapes resolve
  // regardless of spelling (the identify fix). No-op if already present.
  function ensureStashId(performerId, nameId) {
    if (!nameId) return Promise.resolve();
    return gql(
      "query ($id: ID!) { findPerformer(id: $id) { id stash_ids { endpoint stash_id } } }",
      { id: String(performerId) }
    ).then(function (d) {
      var p = d && d.findPerformer;
      if (!p) return;
      var sid = String(nameId);
      var existing = p.stash_ids || [];
      if (existing.some(function (s) { return s.endpoint === ENDPOINT && s.stash_id === sid; })) {
        return;
      }
      var list = existing.map(function (s) {
        return { endpoint: s.endpoint, stash_id: s.stash_id };
      });
      list.push({ endpoint: ENDPOINT, stash_id: sid });
      return gql(
        "mutation ($input: PerformerUpdateInput!) { performerUpdate(input: $input) { id } }",
        { input: { id: String(performerId), stash_ids: list } }
      );
    });
  }

  function associate(imageId, performerIds) {
    return gql(
      "mutation ($input: ImageUpdateInput!) { imageUpdate(input: $input) {" +
        " id performers { id name } } }",
      { input: { id: String(imageId), performer_ids: performerIds } }
    ).then(function (d) {
      return (d && d.imageUpdate) || null;
    });
  }

  // Add one performer to many images in a single call (merge-add; idempotent).
  function bulkAssociate(imageIds, performerId) {
    return gql(
      "mutation ($input: BulkImageUpdateInput!) { bulkImageUpdate(input: $input) { id } }",
      {
        input: {
          ids: imageIds.map(String),
          performer_ids: { ids: [String(performerId)], mode: "ADD" },
        },
      }
    ).then(function (d) {
      return (d && d.bulkImageUpdate) || null;
    });
  }

  // =========================================================================
  // Small utilities (view state persistence, tag value shims) — from tag-manager.js
  // =========================================================================

  function loadSetting(key, fallback) {
    try {
      var raw = localStorage.getItem(STORAGE_PREFIX + key);
      return raw !== null ? JSON.parse(raw) : fallback;
    } catch (_) {
      return fallback;
    }
  }
  function saveSetting(key, value) {
    try {
      localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(value));
    } catch (_) {
      /* quota — ignore */
    }
  }
  function useLocalStorage(key, fallback) {
    var pair = useState(function () {
      return loadSetting(key, fallback);
    });
    React.useEffect(
      function () {
        saveSetting(key, pair[0]);
      },
      [key, pair[0]]
    );
    return pair;
  }

  function useAllTags() {
    var result = GQL.useFindTagsQuery({
      variables: { filter: { per_page: -1, sort: "name", direction: GQL.SortDirectionEnum.Asc } },
    });
    return useMemo(
      function () {
        if (!result.data || !result.data.findTags) return [];
        return result.data.findTags.tags;
      },
      [result.data]
    );
  }

  function idsToTagValues(ids, allTags) {
    var map = {};
    allTags.forEach(function (t) {
      map[t.id] = t;
    });
    return ids.map(function (id) {
      return map[id] || { id: id, name: id };
    });
  }

  function formatDate(dateStr) {
    if (!dateStr) return "";
    try {
      return new Date(dateStr).toLocaleDateString();
    } catch (_) {
      return dateStr;
    }
  }

  // =========================================================================
  // TagIncludeExcludeControl — unified [+]/[-] tag picker (from tag-manager.js)
  // =========================================================================

  function TagIncludeExcludeControl(props) {
    var allTags = props.allTags;
    var includedIds = props.includedIds;
    var excludedIds = props.excludedIds;
    var onIncludedChange = props.onIncludedChange;
    var onExcludedChange = props.onExcludedChange;

    var searchState = useState("");
    var search = searchState[0];
    var setSearch = searchState[1];

    var includedSet = useMemo(
      function () {
        return new Set(includedIds);
      },
      [includedIds]
    );
    var excludedSet = useMemo(
      function () {
        return new Set(excludedIds);
      },
      [excludedIds]
    );

    var filteredTags = useMemo(
      function () {
        if (!search) return allTags;
        var lower = search.toLowerCase();
        return allTags.filter(function (t) {
          return t.name.toLowerCase().indexOf(lower) >= 0;
        });
      },
      [allTags, search]
    );

    var tagNameMap = useMemo(
      function () {
        var m = {};
        allTags.forEach(function (t) {
          m[t.id] = t.name;
        });
        return m;
      },
      [allTags]
    );

    function toggleInclude(id) {
      if (includedSet.has(id)) {
        onIncludedChange(includedIds.filter((x) => x !== id));
      } else {
        if (excludedSet.has(id)) onExcludedChange(excludedIds.filter((x) => x !== id));
        onIncludedChange(includedIds.concat(id));
      }
    }
    function toggleExclude(id) {
      if (excludedSet.has(id)) {
        onExcludedChange(excludedIds.filter((x) => x !== id));
      } else {
        if (includedSet.has(id)) onIncludedChange(includedIds.filter((x) => x !== id));
        onExcludedChange(excludedIds.concat(id));
      }
    }
    function removePill(id) {
      if (includedSet.has(id)) onIncludedChange(includedIds.filter((x) => x !== id));
      else if (excludedSet.has(id)) onExcludedChange(excludedIds.filter((x) => x !== id));
    }

    var pills = [];
    includedIds.forEach(function (id) {
      pills.push(
        el(
          Badge,
          {
            key: "i-" + id,
            className: "spid-tag-included",
            onClick: function () {
              removePill(id);
            },
            style: { cursor: "pointer" },
          },
          (tagNameMap[id] || id) + " ×"
        )
      );
    });
    excludedIds.forEach(function (id) {
      pills.push(
        el(
          Badge,
          {
            key: "e-" + id,
            className: "spid-tag-excluded",
            onClick: function () {
              removePill(id);
            },
            style: { cursor: "pointer" },
          },
          (tagNameMap[id] || id) + " ×"
        )
      );
    });

    return el(
      "div",
      null,
      pills.length > 0 && el("div", { className: "spid-tag-pills" }, pills),
      el(
        "div",
        { className: "spid-tag-control" },
        el(
          "div",
          { className: "spid-tag-control-search" },
          el(Form.Control, {
            className: "btn-secondary",
            placeholder: "Search…",
            value: search,
            onChange: function (e) {
              setSearch(e.target.value);
            },
          })
        ),
        filteredTags.map(function (tag) {
          var isIncluded = includedSet.has(tag.id);
          var isExcluded = excludedSet.has(tag.id);
          return el(
            "div",
            { key: tag.id, className: "spid-tag-row" },
            el(
              Button,
              {
                variant: "link",
                size: "sm",
                className: "spid-btn-include" + (isIncluded ? " active" : ""),
                onClick: function () {
                  toggleInclude(tag.id);
                },
                title: "Include",
              },
              "+"
            ),
            el(
              "span",
              {
                className:
                  "spid-tag-name" +
                  (isIncluded ? " text-success" : isExcluded ? " text-danger" : ""),
              },
              tag.name
            ),
            el(
              Button,
              {
                variant: "link",
                size: "sm",
                className: "spid-btn-exclude" + (isExcluded ? " active" : ""),
                onClick: function () {
                  toggleExclude(tag.id);
                },
                title: "Exclude",
              },
              "−"
            )
          );
        })
      )
    );
  }

  // =========================================================================
  // EditFilterModal — Tags / Path / Organized (adapted from tag-manager.js)
  // =========================================================================

  function EditFilterModal(props) {
    var allTags = props.allTags;
    var Icon = api.components.Icon;

    var openSectionState = useState("tags");
    var openSection = openSectionState[0];
    var setOpenSection = openSectionState[1];

    var hasAnyFilter =
      props.filterTagIncludeIds.length > 0 ||
      props.filterTagExcludeIds.length > 0 ||
      props.filterPath ||
      props.filterOrganized !== null;

    function handleClear() {
      props.onFilterTagIncludeChange([]);
      props.onFilterTagExcludeChange([]);
      props.onFilterTagDepthChange(0);
      props.onFilterPathChange("");
      props.onFilterOrganizedChange(null);
    }
    function clearSection(key) {
      if (key === "tags") {
        props.onFilterTagIncludeChange([]);
        props.onFilterTagExcludeChange([]);
        props.onFilterTagDepthChange(0);
      }
      if (key === "path") props.onFilterPathChange("");
      if (key === "organized") props.onFilterOrganizedChange(null);
    }

    function filterCard(key, label, hasValue, body) {
      return el(
        Card,
        { key: key },
        el(
          Accordion.Toggle,
          { eventKey: key, className: "spid-filter-header" },
          el(
            "span",
            { className: "mr-auto" },
            el(Icon, {
              className: "fa-fw",
              icon: openSection === key ? FA.faChevronDown : FA.faChevronRight,
            }),
            " " + label
          ),
          hasValue &&
            el(
              Button,
              {
                variant: "minimal",
                onClick: function (e) {
                  e.stopPropagation();
                  clearSection(key);
                },
              },
              el(Icon, { icon: FA.faTimes })
            )
        ),
        el(Accordion.Collapse, { eventKey: key }, el(Card.Body, null, body))
      );
    }

    return el(
      Modal,
      { show: props.show, onHide: props.onClose, className: "spid-filter-dialog" },
      el(Modal.Header, null, el("div", null, "Edit Filter")),
      el(
        Modal.Body,
        null,
        el(
          Accordion,
          {
            activeKey: openSection,
            onSelect: function (k) {
              setOpenSection(k === openSection ? null : k);
            },
          },
          filterCard(
            "tags",
            "Tags",
            props.filterTagIncludeIds.length + props.filterTagExcludeIds.length > 0,
            el(
              "div",
              null,
              el(Form.Check, {
                type: "checkbox",
                label: "Include sub-tags",
                checked: props.filterTagDepth === -1,
                onChange: function (e) {
                  props.onFilterTagDepthChange(e.target.checked ? -1 : 0);
                },
                id: "spid-filter-tag-depth",
                className: "mb-2",
              }),
              el(TagIncludeExcludeControl, {
                allTags: allTags,
                includedIds: props.filterTagIncludeIds,
                excludedIds: props.filterTagExcludeIds,
                onIncludedChange: props.onFilterTagIncludeChange,
                onExcludedChange: props.onFilterTagExcludeChange,
              })
            )
          ),
          filterCard(
            "path",
            "Path",
            !!props.filterPath,
            el(
              "div",
              null,
              el(
                Form.Group,
                { className: "spid-modifier-options" },
                [GQL.CriterionModifier.Includes, GQL.CriterionModifier.MatchesRegex].map(
                  function (m) {
                    var label = m === GQL.CriterionModifier.Includes ? "includes" : "matches regex";
                    return el(
                      Button,
                      {
                        key: m,
                        variant: props.filterPathModifier === m ? "primary" : "secondary",
                        size: "sm",
                        className: "mr-2",
                        onClick: function () {
                          props.onFilterPathModifierChange(m);
                        },
                      },
                      label
                    );
                  }
                )
              ),
              el(Form.Control, {
                className: "btn-secondary",
                placeholder: "File path…",
                value: props.filterPath,
                onChange: function (e) {
                  props.onFilterPathChange(e.target.value);
                },
              })
            )
          ),
          filterCard(
            "organized",
            "Organized",
            props.filterOrganized !== null,
            el(
              "div",
              null,
              [
                { label: "Any", value: null },
                { label: "Organized", value: true },
                { label: "Not Organized", value: false },
              ].map(function (opt, i) {
                return el(Form.Check, {
                  key: i,
                  type: "radio",
                  label: opt.label,
                  name: "spid-org",
                  checked: props.filterOrganized === opt.value,
                  onChange: function () {
                    props.onFilterOrganizedChange(opt.value);
                  },
                  id: "spid-org-" + i,
                });
              })
            )
          )
        )
      ),
      el(
        Modal.Footer,
        null,
        hasAnyFilter && el(Button, { variant: "secondary", onClick: handleClear }, "Clear All"),
        el(Button, { onClick: props.onClose }, "Close")
      )
    );
  }

  // =========================================================================
  // SortControl / PerPageSelect / PaginationNav (from tag-manager.js)
  // =========================================================================

  function SortControl(props) {
    var Icon = api.components.Icon;
    return el(
      Dropdown,
      { as: Bootstrap.ButtonGroup, className: "sort-by-select" },
      el(
        Dropdown.Toggle,
        { variant: "secondary" },
        (
          SORT_OPTIONS.find(function (o) {
            return o.value === props.sortField;
          }) || {}
        ).label || props.sortField
      ),
      el(
        Dropdown.Menu,
        { className: "bg-secondary text-white" },
        SORT_OPTIONS.map(function (opt) {
          return el(
            Dropdown.Item,
            {
              key: opt.value,
              active: opt.value === props.sortField,
              className: "bg-secondary text-white",
              onClick: function () {
                props.onSortFieldChange(opt.value);
              },
            },
            opt.label
          );
        })
      ),
      el(
        Button,
        {
          variant: "secondary",
          onClick: props.onSortDirToggle,
          title: props.sortDir === "DESC" ? "Descending" : "Ascending",
        },
        el(Icon, { icon: props.sortDir === "DESC" ? FA.faSortAmountDown : FA.faSortAmountUp })
      )
    );
  }

  function PerPageSelect(props) {
    return el(
      Form.Control,
      {
        as: "select",
        className: "btn-secondary page-size-selector",
        value: props.value,
        onChange: function (e) {
          props.onChange(Number(e.target.value));
        },
      },
      PER_PAGE_OPTIONS.map(function (n) {
        return el("option", { key: n, value: n }, n);
      })
    );
  }

  function PaginationNav(props) {
    var currentPage = props.currentPage;
    var totalPages = props.totalPages;
    var totalItems = props.totalItems;
    var perPage = props.perPage;
    if (totalPages <= 1 && !totalItems) return null;

    var countText = null;
    if (totalItems != null) {
      var start = totalItems === 0 ? 0 : (currentPage - 1) * perPage + 1;
      var end = Math.min(currentPage * perPage, totalItems);
      countText = el(
        "span",
        { className: "filter-container text-muted paginationIndex center-text" },
        start + "-" + end + " of " + totalItems
      );
    }
    function navBtn(label, page, disabled) {
      return el(
        Button,
        {
          key: label,
          variant: "secondary",
          disabled: disabled,
          onClick: function () {
            props.onPageChange(page);
          },
        },
        label
      );
    }
    var btnGroup =
      totalPages > 1 &&
      el(
        "div",
        { className: "pagination btn-group" },
        navBtn("«", 1, currentPage === 1),
        navBtn("‹", currentPage - 1, currentPage === 1),
        el(
          "div",
          { className: "page-count-container" },
          el(
            "div",
            { className: "btn-group" },
            el(Button, { variant: "secondary" }, currentPage + " of " + totalPages)
          )
        ),
        navBtn("›", currentPage + 1, currentPage === totalPages),
        navBtn("»", totalPages, currentPage === totalPages)
      );

    // Bottom pager uses the native sticky footer structure (matches the scene list).
    if (props.footer) {
      if (totalPages <= 1) return null;
      return el(
        "div",
        { className: "pagination-footer-container" },
        el("div", { className: "pagination-footer" }, btnGroup)
      );
    }
    return el("div", { className: "pagination-index-container" }, btnGroup, countText);
  }

  // =========================================================================
  // PerformerCreateModal — Stash's PerformerModal (create mode) rebuilt with the
  // native CSS classes: enriched fields on the left, image carousel on the right.
  // =========================================================================

  // Fields shown, in Stash's PerformerModal order. `list` = render as a URL list.
  var CREATE_FIELDS = [
    { key: "disambiguation", label: "Disambiguation" },
    { key: "aliases", label: "Aliases" },
    { key: "gender", label: "Gender" },
    { key: "birthdate", label: "Birthdate" },
    { key: "death_date", label: "Death Date" },
    { key: "ethnicity", label: "Ethnicity" },
    { key: "country", label: "Country" },
    { key: "hair_color", label: "Hair Color" },
    { key: "eye_color", label: "Eye Color" },
    { key: "height", label: "Height" },
    { key: "weight", label: "Weight" },
    { key: "measurements", label: "Measurements" },
    { key: "fake_tits", label: "Fake Tits" },
    { key: "tattoos", label: "Tattoos" },
    { key: "piercings", label: "Piercings" },
    { key: "details", label: "Details" },
    { key: "urls", label: "URLs", list: true },
  ];

  function PerformerCreateModal(props) {
    var scraped = props.scraped;
    var Modal = Bootstrap.Modal;
    var Icon = api.components.Icon;
    var imgs = scraped.images || [];
    var idxS = useState(0);
    var imgIdx = idxS[0];
    var setImgIdx = idxS[1];
    var busyS = useState(false);
    var busy = busyS[0];
    var setBusy = busyS[1];
    var errS = useState(null);
    var err = errS[0];
    var setErr = errS[1];

    function fieldRow(label, value, key) {
      if (value == null || value === "") return null;
      return el(
        "div",
        { className: "row no-gutters", key: key || label },
        el("div", { className: "col-5 create-modal-field" }, el("strong", null, label + ":")),
        el("div", { className: "col-7 create-modal-value" }, value)
      );
    }
    function urlsRow(urls) {
      if (!urls || !urls.length) return null;
      return el(
        "div",
        { className: "row no-gutters", key: "urls" },
        el("div", { className: "col-5 create-modal-field" }, el("strong", null, "URLs:")),
        el(
          "div",
          { className: "col-7 create-modal-value" },
          el(
            "ul",
            null,
            urls.map(function (u, i) {
              return el(
                "li",
                { key: i },
                el("a", { href: u, target: "_blank", rel: "noreferrer" }, u)
              );
            })
          )
        )
      );
    }

    function save() {
      setBusy(true);
      setErr(null);
      createPerformer(scraped, {}, imgs.length ? imgs[imgIdx] : undefined)
        .then(function (p) {
          setBusy(false);
          if (p) props.onCreated(p);
          else setErr("performerCreate failed");
        })
        .catch(function (e) {
          setBusy(false);
          setErr(String(e));
        });
    }

    var fieldNodes = [fieldRow("Name", scraped.name, "name")];
    CREATE_FIELDS.forEach(function (f) {
      fieldNodes.push(f.list ? urlsRow(scraped[f.key]) : fieldRow(f.label, scraped[f.key], f.key));
    });

    return el(
      Modal,
      {
        show: true,
        onHide: props.onClose,
        dialogClassName: "performer-create-modal",
        size: "lg",
      },
      el(
        Modal.Header,
        { closeButton: true },
        el(Modal.Title, null, "Create performer: " + scraped.name)
      ),
      el(
        Modal.Body,
        null,
        err && el("div", { className: "text-danger font-weight-bold mb-2" }, err),
        el(
          "div",
          { className: "row" },
          el("div", { className: "col-7" }, fieldNodes),
          imgs.length
            ? el(
                "div",
                { className: "col-5 image-selection" },
                el(
                  "div",
                  { className: "performer-image" },
                  el("img", { src: imgs[imgIdx], alt: "" })
                ),
                el(
                  "div",
                  { className: "d-flex mt-3" },
                  el(
                    Button,
                    {
                      onClick: function () {
                        setImgIdx(function (i) {
                          return (i - 1 + imgs.length) % imgs.length;
                        });
                      },
                      disabled: imgs.length === 1,
                    },
                    el(Icon, { icon: FA.faArrowLeft })
                  ),
                  el(
                    "h5",
                    { className: "flex-grow-1 text-center" },
                    "Select performer image",
                    el("br"),
                    imgIdx + 1 + " of " + imgs.length
                  ),
                  el(
                    Button,
                    {
                      onClick: function () {
                        setImgIdx(function (i) {
                          return (i + 1) % imgs.length;
                        });
                      },
                      disabled: imgs.length === 1,
                    },
                    el(Icon, { icon: FA.faArrowRight })
                  )
                )
              )
            : null
        )
      ),
      el(
        Modal.Footer,
        null,
        el(Button, { variant: "secondary", onClick: props.onClose, disabled: busy }, "Cancel"),
        el(
          Button,
          { variant: "primary", onClick: save, disabled: busy },
          busy ? el(Spinner, { animation: "border", size: "sm" }) : "Create"
        )
      )
    );
  }

  // =========================================================================
  // ImageCard — a scene-tagger-style card: header (thumb / title / current /
  // Scrape) with a Collapse slidedown for the assignment (suggestion + picker).
  // =========================================================================

  function ImageCard(props) {
    var image = props.image;
    var st = props.rowState || {}; // { scraped, selected, status, message, action, open }
    var PerformerSelect = api.components.PerformerSelect;
    var Icon = api.components.Icon;
    var Collapse = Bootstrap.Collapse;

    var filePath = image.visual_files && image.visual_files[0] ? image.visual_files[0].path : "";
    var thumb = image.paths && image.paths.thumbnail;
    var current = image.performers || [];
    var scraped = st.scraped;
    var selected = st.selected;
    var busy = st.status === "busy";
    var open = !!st.open;

    // --- assignment body (shown in the slidedown once scraped) ---
    // Scene-tagger layout: the provider suggestion is a thumbnail + name caption on the left;
    // the select / create / save controls float to the right.
    function renderBody() {
      var providerThumb = scraped.images && scraped.images[0];
      // Bounded thumbnail on the left; to its right a column with the name caption on top and the
      // select/create/save controls below (so a long name isn't clamped to the thumbnail width).
      var thumbEl = el(
        "div",
        { key: "th", className: "spid-suggest-thumb" },
        providerThumb ? el("img", { src: providerThumb, loading: "lazy", alt: "" }) : null
      );
      var nameCap = el(
        "div",
        { key: "nm", className: "spid-suggest-name" },
        el("span", { className: "spid-suggest-label" }, "Suggested: "),
        scraped.name || "(no match)",
        scraped.disambiguation
          ? el("span", { className: "spid-suggest-disamb" }, " (" + scraped.disambiguation + ")")
          : null
      );

      var controls = [];
      if (PerformerSelect) {
        controls.push(
          el(
            "div",
            { key: "ps", className: "spid-perf-select mr-2" },
            el(PerformerSelect, {
              isMulti: false,
              isClearable: true,
              values: selected ? [selected] : [],
              onSelect: function (items) {
                props.onSelect(image, items && items.length ? items[0] : null);
              },
            })
          )
        );
      }
      if (!selected && scraped.name) {
        controls.push(
          el(
            Button,
            {
              key: "cr",
              variant: "outline-success",
              size: "sm",
              className: "mr-2",
              disabled: busy,
              title: "Review the enriched fields and create the performer",
              onClick: function () {
                props.onOpenCreate(image);
              },
            },
            "Create…"
          )
        );
      }
      controls.push(
        el(
          Button,
          {
            key: "sv",
            variant: "primary",
            size: "sm",
            disabled: busy || !selected,
            onClick: function () {
              props.onSave(image);
            },
          },
          busy && st.action === "save" ? el(Spinner, { animation: "border", size: "sm" }) : "Save"
        )
      );
      if (st.status === "error") {
        controls.push(
          el(
            "span",
            { key: "er", className: "spid-status text-danger ml-2", title: st.message },
            "! " + (st.message || "error")
          )
        );
      }
      return el(
        "div",
        { className: "spid-assign" },
        thumbEl,
        el(
          "div",
          { className: "spid-assign-main" },
          nameCap,
          el(
            "div",
            { key: "ctl", className: "spid-assign-controls d-flex align-items-center" },
            controls
          )
        )
      );
    }

    return el(
      "div",
      { className: "search-item mt-3" },
      // --- header row ---
      el(
        "div",
        { className: "spid-card-head d-flex align-items-center" },
        el(
          "label",
          { className: "mb-0 mr-3" },
          el("input", {
            type: "checkbox",
            className: "search-item-check",
            checked: !!props.checked,
            onChange: function () {
              props.onToggle(image.id);
            },
          })
        ),
        el(
          Link,
          { to: "/images/" + image.id, className: "spid-card-thumb mr-3" },
          thumb ? el("img", { className: "image-thumbnail", src: thumb, loading: "lazy" }) : null
        ),
        el(
          "div",
          { className: "spid-card-info flex-grow-1 overflow-hidden" },
          el(
            "div",
            { className: "spid-card-title ellips-data" },
            image.title || (filePath ? filePath.split("/").pop() : image.id)
          ),
          filePath && el("div", { className: "spid-card-path text-muted" }, filePath),
          el(
            "div",
            { className: "spid-card-current mt-1" },
            current.length
              ? current.map(function (p) {
                  return el(Badge, { key: p.id, className: "spid-perf-badge" }, p.name);
                })
              : el("span", { className: "text-muted small" }, "No performers")
          )
        ),
        el(
          "div",
          { className: "spid-card-actions d-flex align-items-center" },
          st.status === "saved" && el("span", { className: "text-success mr-2" }, "✓ saved"),
          el(
            Button,
            {
              variant: "secondary",
              size: "sm",
              disabled: busy,
              onClick: function () {
                props.onScrape(image);
              },
            },
            busy && st.action === "scrape"
              ? el(Spinner, { animation: "border", size: "sm" })
              : "Scrape"
          ),
          scraped &&
            el(
              Button,
              {
                className: "minimal collapse-button ml-1",
                title: open ? "Collapse" : "Expand",
                onClick: function () {
                  props.onToggleOpen(image.id);
                },
              },
              el(Icon, { icon: open ? FA.faChevronUp : FA.faChevronDown })
            )
        )
      ),
      // --- slidedown assignment ---
      el(
        Collapse,
        { in: open },
        el(
          "div",
          null,
          el(
            "div",
            { className: "spid-card-body d-flex align-items-center flex-wrap" },
            scraped ? renderBody() : null
          )
        )
      )
    );
  }

  // =========================================================================
  // ImageTaggerPageInner
  // =========================================================================

  function ImageTaggerPageInner() {
    var pageState = useState(1);
    var page = pageState[0];
    var setPage = pageState[1];

    var perPageLS = useLocalStorage("perPage", 40);
    var perPage = perPageLS[0];
    var setPerPage = perPageLS[1];
    var sortFieldLS = useLocalStorage("sortField", "path");
    var sortField = sortFieldLS[0];
    var setSortField = sortFieldLS[1];
    var sortDirLS = useLocalStorage("sortDir", "ASC");
    var sortDir = sortDirLS[0];
    var setSortDir = sortDirLS[1];

    var allTags = useAllTags();
    var toast = api.hooks.useToast();

    // Per-row tagger state, keyed by image id.
    var rowStateS = useState({});
    var rowState = rowStateS[0];
    var setRowState = rowStateS[1];

    function patchRow(id, patch) {
      setRowState(function (prev) {
        var next = Object.assign({}, prev);
        next[id] = Object.assign({}, prev[id], patch);
        return next;
      });
    }

    // Row selection (checkboxes) + batch progress.
    var selS = useState(function () {
      return new Set();
    });
    var selected = selS[0];
    var setSelected = selS[1];
    var progS = useState(null); // null | { done, total, saving? }
    var progress = progS[0];
    var setProgress = progS[1];
    var stoppingRef = useRef(false);
    var createTargetS = useState(null); // { image, scraped } for the create modal
    var createTarget = createTargetS[0];
    var setCreateTarget = createTargetS[1];

    // Filters
    var filterOpenS = useState(false);
    var filterOpen = filterOpenS[0];
    var setFilterOpen = filterOpenS[1];
    var tagIncS = useState([]);
    var tagExcS = useState([]);
    var tagDepthS = useState(0);
    var pathS = useState("");
    var pathModS = useState(GQL.CriterionModifier.Includes);
    var orgS = useState(null);

    var filterTagIncludeIds = tagIncS[0];
    var filterTagExcludeIds = tagExcS[0];
    var filterTagDepth = tagDepthS[0];
    var filterPath = pathS[0];
    var filterPathModifier = pathModS[0];
    var filterOrganized = orgS[0];

    var direction = sortDir === "DESC" ? GQL.SortDirectionEnum.Desc : GQL.SortDirectionEnum.Asc;

    var imageFilter = useMemo(
      function () {
        var f = {};
        var has = false;
        if (filterTagIncludeIds.length > 0) {
          f.tags = {
            value: filterTagIncludeIds,
            modifier: GQL.CriterionModifier.IncludesAll,
            depth: filterTagDepth,
          };
          has = true;
        }
        if (filterTagExcludeIds.length > 0) {
          var exc = {
            value: filterTagExcludeIds,
            modifier: GQL.CriterionModifier.Excludes,
            depth: filterTagDepth,
          };
          if (f.tags) f.AND = { tags: exc };
          else f.tags = exc;
          has = true;
        }
        if (filterPath) {
          f.path = { value: filterPath, modifier: filterPathModifier };
          has = true;
        }
        if (filterOrganized !== null) {
          f.organized = filterOrganized;
          has = true;
        }
        return has ? f : undefined;
      },
      [
        filterTagIncludeIds,
        filterTagExcludeIds,
        filterTagDepth,
        filterPath,
        filterPathModifier,
        filterOrganized,
      ]
    );

    var queryResult = GQL.useFindImagesQuery({
      variables: {
        filter: { page: page, per_page: perPage, sort: sortField, direction: direction },
        image_filter: imageFilter,
      },
      fetchPolicy: "cache-and-network",
    });

    var data = queryResult.data;
    var loading = queryResult.loading;
    var images = data && data.findImages ? data.findImages.images : [];
    var totalCount = data && data.findImages ? data.findImages.count : 0;
    var totalPages = Math.ceil(totalCount / perPage) || 1;

    // Reset selection when the visible page/filter changes.
    React.useEffect(
      function () {
        setSelected(new Set());
      },
      [page, perPage, sortField, sortDir, imageFilter]
    );

    // --- row handlers ---

    // Scrape one image: fetch the suggestion and auto-resolve to an existing performer.
    // Returns a promise (reused by the batch loop).
    function scrapeOne(image) {
      patchRow(image.id, { status: "busy", action: "scrape", message: null });
      return scrapeImage(image.id)
        .then(function (scraped) {
          if (!scraped) {
            patchRow(image.id, {
              status: "idle",
              action: null,
              scraped: { name: null },
              selected: null,
              open: true,
            });
            return;
          }
          return resolveScraped(scraped).then(function (match) {
            patchRow(image.id, {
              status: "idle",
              action: null,
              scraped: scraped,
              selected: match || null,
              open: true,
            });
          });
        })
        .catch(function (e) {
          patchRow(image.id, { status: "error", action: null, message: String(e) });
        });
    }

    function onScrape(image) {
      scrapeOne(image);
    }

    function onSelect(image, performer) {
      patchRow(image.id, { selected: performer, status: "idle" });
    }

    function onToggleOpen(id) {
      var st = rowState[id];
      patchRow(id, { open: !(st && st.open) });
    }

    // Open the create-review modal for a scraped image (enriched fields + image carousel).
    function onOpenCreate(image) {
      var st = rowState[image.id];
      if (st && st.scraped && st.scraped.name) {
        setCreateTarget({ image: image, scraped: st.scraped });
      }
    }

    // Merge one image's selected performer (returns a promise). Stamps the chosen performer's
    // stash_ids with the name-record id first, so future scrapes auto-resolve (the identify fix).
    function saveOne(image, sel) {
      var st = rowState[image.id];
      var nameId = st && st.scraped && st.scraped.remote_site_id;
      var existing = (image.performers || []).map(function (p) {
        return p.id;
      });
      var ids = existing.slice();
      if (ids.indexOf(sel.id) < 0) ids.push(sel.id);
      patchRow(image.id, { status: "busy", action: "save" });
      return ensureStashId(sel.id, nameId)
        .then(function () {
          return associate(image.id, ids);
        })
        .then(function (updated) {
          if (updated) patchRow(image.id, { status: "saved", action: null });
          else patchRow(image.id, { status: "error", action: null, message: "imageUpdate failed" });
        })
        .catch(function (e) {
          patchRow(image.id, { status: "error", action: null, message: String(e) });
        });
    }

    function onSave(image) {
      var st = rowState[image.id];
      if (!st || !st.selected) return;
      var sel = st.selected;
      saveOne(image, sel).then(function () {
        toast.toast({ content: "Saved " + (sel.name || "performer") });
        queryResult.refetch();
      });
    }

    // --- selection ---
    function toggleRow(id) {
      setSelected(function (prev) {
        var n = new Set(prev);
        if (n.has(id)) n.delete(id);
        else n.add(id);
        return n;
      });
    }
    function selectAll() {
      var n = new Set();
      images.forEach(function (im) {
        n.add(im.id);
      });
      setSelected(n);
    }
    function selectNone() {
      setSelected(new Set());
    }
    function invertSelection() {
      setSelected(function (prev) {
        var n = new Set();
        images.forEach(function (im) {
          if (!prev.has(im.id)) n.add(im.id);
        });
        return n;
      });
    }
    // Batch target: checked rows on this page, or the whole page when nothing is checked.
    function targetImages() {
      if (selected.size === 0) return images;
      return images.filter(function (im) {
        return selected.has(im.id);
      });
    }

    // --- batch scrape (sequential + cancellable — SceneTagger's method) ---
    function onScrapeBatch() {
      var targets = targetImages();
      if (!targets.length || progress) return;
      stoppingRef.current = false;
      setProgress({ done: 0, total: targets.length });
      targets
        .reduce(function (p, image) {
          return p.then(function () {
            if (stoppingRef.current) return;
            return scrapeOne(image).then(function () {
              setProgress(function (pr) {
                return pr ? { done: pr.done + 1, total: pr.total } : pr;
              });
            });
          });
        }, Promise.resolve())
        .then(function () {
          setProgress(null);
        });
    }
    function onStop() {
      stoppingRef.current = true;
    }

    // A row is savable once scraped: either a resolved existing performer, or a new name to create.
    function rowTarget(im) {
      var st = rowState[im.id];
      if (!st) return null;
      var nameId = st.scraped && st.scraped.remote_site_id;
      if (st.selected) {
        return { key: "id:" + st.selected.id, id: st.selected.id, scraped: null, nameId: nameId, imageId: im.id };
      }
      if (st.scraped && st.scraped.name) {
        return { key: "new:" + (nameId || st.scraped.name), id: null, scraped: st.scraped, nameId: nameId, imageId: im.id };
      }
      return null;
    }

    // --- batch save: group by performer, create each distinct new one once, then bulk-associate ---
    function onSaveBatch() {
      var rows = targetImages()
        .map(rowTarget)
        .filter(Boolean);
      if (!rows.length || progress) return;
      var groups = {};
      rows.forEach(function (r) {
        var g =
          groups[r.key] ||
          (groups[r.key] = { id: r.id, scraped: r.scraped, nameId: r.nameId, imageIds: [] });
        g.imageIds.push(r.imageId);
      });
      setProgress({ done: 0, total: rows.length, saving: true });
      Object.keys(groups)
        .reduce(function (p, key) {
          return p.then(function () {
            var g = groups[key];
            var idP = g.id
              ? Promise.resolve(g.id)
              : createPerformer(g.scraped).then(function (perf) {
                  return perf ? perf.id : null;
                });
            return idP
              .then(function (pid) {
                if (!pid) {
                  g.imageIds.forEach(function (id) {
                    patchRow(id, { status: "error", message: "performerCreate failed" });
                  });
                  return;
                }
                // Existing performers get stamped with our stash_id; created ones already have it.
                var stamp = g.id ? ensureStashId(pid, g.nameId) : Promise.resolve();
                return stamp
                  .then(function () {
                    return bulkAssociate(g.imageIds, pid);
                  })
                  .then(function () {
                    g.imageIds.forEach(function (id) {
                      patchRow(id, { status: "saved", action: null });
                    });
                  });
              })
              .then(function () {
                setProgress(function (pr) {
                  return pr
                    ? { done: pr.done + g.imageIds.length, total: pr.total, saving: true }
                    : pr;
                });
              });
          });
        }, Promise.resolve())
        .then(function () {
          setProgress(null);
          toast.toast({ content: "Saved " + rows.length + " image(s)" });
          queryResult.refetch();
        })
        .catch(function (e) {
          setProgress(null);
          toast.toast({ variant: "danger", content: "Batch save failed: " + e });
        });
    }

    // Counts for the toolbar batch buttons.
    var scopeCount = selected.size || images.length;
    var savableCount = targetImages().filter(function (im) {
      return rowTarget(im) !== null;
    }).length;

    // --- toolbar handlers ---
    function resetToFirst(fn) {
      setPage(1);
      fn();
    }

    var hasActiveFilter = imageFilter != null;

    return el(
      "div",
      { className: "spid-page tagger-container mx-auto" },

      // View controls (native list toolbar classes)
      el(
        "div",
        { className: "filtered-list-toolbar btn-toolbar", role: "toolbar" },
        el(
          Button,
          {
            variant: hasActiveFilter ? "primary" : "secondary",
            className: "mr-2",
            onClick: function () {
              setFilterOpen(!filterOpen);
            },
            title: "Edit Filter",
          },
          el(api.components.Icon, { icon: FA.faFilter }),
          " Filter"
        ),
        el(SortControl, {
          sortField: sortField,
          sortDir: sortDir,
          onSortFieldChange: function (f) {
            resetToFirst(function () {
              setSortField(f);
            });
          },
          onSortDirToggle: function () {
            resetToFirst(function () {
              setSortDir(sortDir === "DESC" ? "ASC" : "DESC");
            });
          },
        }),
        el(PerPageSelect, {
          value: perPage,
          onChange: function (n) {
            resetToFirst(function () {
              setPerPage(n);
            });
          },
        }),
        el(
          Button,
          {
            variant: "secondary",
            onClick: function () {
              queryResult.refetch();
            },
            title: "Refresh",
          },
          el(api.components.Icon, { icon: FA.faSync })
        ),

        // Selection operations ("…" menu — matches the scene tagger's ListOperations).
        el(
          Dropdown,
          { as: Bootstrap.ButtonGroup, className: "list-operations ml-2" },
          el(
            Dropdown.Toggle,
            { variant: "secondary", id: "more-menu", title: "Selection" },
            el(api.components.Icon, { icon: FA.faEllipsisH })
          ),
          el(
            Dropdown.Menu,
            { className: "bg-secondary text-white" },
            el(
              Dropdown.Item,
              { className: "bg-secondary text-white", onClick: selectAll },
              "Select All"
            ),
            el(
              Dropdown.Item,
              { className: "bg-secondary text-white", onClick: selectNone },
              "Select None"
            ),
            el(
              Dropdown.Item,
              { className: "bg-secondary text-white", onClick: invertSelection },
              "Invert Selection"
            )
          )
        )
      ),

      el(EditFilterModal, {
        show: filterOpen,
        allTags: allTags,
        filterTagIncludeIds: filterTagIncludeIds,
        filterTagExcludeIds: filterTagExcludeIds,
        filterTagDepth: filterTagDepth,
        filterPath: filterPath,
        filterPathModifier: filterPathModifier,
        filterOrganized: filterOrganized,
        onFilterTagIncludeChange: function (v) {
          resetToFirst(function () {
            tagIncS[1](v);
          });
        },
        onFilterTagExcludeChange: function (v) {
          resetToFirst(function () {
            tagExcS[1](v);
          });
        },
        onFilterTagDepthChange: tagDepthS[1],
        onFilterPathChange: function (v) {
          resetToFirst(function () {
            pathS[1](v);
          });
        },
        onFilterPathModifierChange: pathModS[1],
        onFilterOrganizedChange: function (v) {
          resetToFirst(function () {
            orgS[1](v);
          });
        },
        onClose: function () {
          setFilterOpen(false);
        },
      }),

      el(PaginationNav, {
        currentPage: page,
        totalPages: totalPages,
        totalItems: totalCount,
        perPage: perPage,
        onPageChange: setPage,
      }),

      // Batch CTA row (scene-tagger's tagger-container-header): below the pagination.
      // Scrape/Save act on the checked rows, or the whole page when nothing is checked.
      el(
        "div",
        {
          className:
            "tagger-container-header d-flex justify-content-between align-items-center flex-wrap",
        },
        el(
          "span",
          { className: "text-muted" },
          selected.size ? selected.size + " selected" : ""
        ),
        el(
          "div",
          { className: "d-flex align-items-center" },
          progress
            ? el(
                React.Fragment,
                null,
                !progress.saving &&
                  el(
                    Button,
                    { variant: "danger", onClick: onStop },
                    el(api.components.Icon, { icon: FA.faStop }),
                    " Stop"
                  ),
                el(
                  "span",
                  { className: "text-muted ml-2" },
                  (progress.saving ? "Saving " : "Scraping ") +
                    progress.done +
                    "/" +
                    progress.total
                )
              )
            : el(
                React.Fragment,
                null,
                el(
                  Button,
                  {
                    variant: "secondary",
                    onClick: onScrapeBatch,
                    disabled: images.length === 0,
                    title:
                      "Scrape " + (selected.size ? selected.size + " selected" : "all on page"),
                  },
                  el(api.components.Icon, { icon: FA.faMagic }),
                  " " + (selected.size ? "Scrape (" + scopeCount + ")" : "Scrape All")
                ),
                el(
                  Button,
                  {
                    variant: "primary",
                    onClick: onSaveBatch,
                    disabled: savableCount === 0,
                    className: "ml-2",
                    title: "Save resolved rows",
                  },
                  el(api.components.Icon, { icon: FA.faSave }),
                  " Save" + (savableCount ? " (" + savableCount + ")" : "")
                )
              )
        )
      ),

      loading && !data
        ? el(
            "div",
            { className: "text-center py-4" },
            el(Spinner, { animation: "border" })
          )
        : images.length === 0
          ? el(
              "div",
              { className: "text-center text-muted py-5" },
              el("h5", null, "No images found"),
              hasActiveFilter && el("p", null, "Try adjusting the filter.")
            )
          : el(
              "div",
              { className: "spid-card-list" },
              images.map(function (image) {
                return el(ImageCard, {
                  key: image.id,
                  image: image,
                  rowState: rowState[image.id],
                  checked: selected.has(image.id),
                  onToggle: toggleRow,
                  onToggleOpen: onToggleOpen,
                  onScrape: onScrape,
                  onSelect: onSelect,
                  onOpenCreate: onOpenCreate,
                  onSave: onSave,
                });
              })
            ),

      el(PaginationNav, {
        currentPage: page,
        totalPages: totalPages,
        totalItems: totalCount,
        perPage: perPage,
        onPageChange: setPage,
        footer: true,
      }),

      // Create-review modal (enriched fields + image carousel), Stash's PerformerModal reused.
      createTarget &&
        el(PerformerCreateModal, {
          scraped: createTarget.scraped,
          onClose: function () {
            setCreateTarget(null);
          },
          onCreated: function (performer) {
            patchRow(createTarget.image.id, { selected: performer, status: "idle" });
            setCreateTarget(null);
          },
        })
    );
  }

  // =========================================================================
  // Loadable gate (PerformerSelect is a loadable component)
  // =========================================================================

  function ImageTaggerPage() {
    var loadingComponents = api.hooks.useLoadComponents([
      api.loadableComponents.PerformerSelect,
    ]);
    if (loadingComponents) {
      return el(
        "div",
        { className: "text-center py-5" },
        el(api.components.LoadingIndicator, null)
      );
    }
    return el(ImageTaggerPageInner, null);
  }

  // =========================================================================
  // Registration
  // =========================================================================

  api.register.route(ROUTE, ImageTaggerPage);

  api.patch.before("MainNavBar.UtilityItems", function (props) {
    var Icon = api.components.Icon;
    return [
      {
        children: el(
          React.Fragment,
          null,
          props.children,
          el(
            NavLink,
            { className: "nav-utility", exact: true, to: ROUTE },
            el(
              Button,
              {
                className: "minimal d-flex align-items-center h-100",
                title: "Performer ID — Image Tagger",
              },
              el(Icon, { icon: FA.faUserTag })
            )
          )
        ),
      },
    ];
  });
})();
