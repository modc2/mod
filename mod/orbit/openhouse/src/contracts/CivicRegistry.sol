// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title CivicRegistry — where a government puts its name on the protocol
/// @notice A directory, not a gate. Any government that wants to supervise
///         rent-to-own deals in its jurisdiction registers here: the key its
///         servers sign with, the jurisdiction it claims, and the URL of the
///         verification server it runs on its own infrastructure. Registration
///         is permissionless because a permissioned list would need someone to
///         own the list, and no city adopts a protocol another party can strike
///         it from.
///
///         IDENTITY IS PROVEN OFF-CHAIN, DELIBERATELY. Nothing on a chain can
///         know that an address is the City of Cleveland. What a government
///         can do is publish this address on infrastructure only it controls —
///         its .gov domain, the `uri` it registers here — so anyone can close
///         the loop: the registry says the key claims the city, the city's own
///         site says the key is the city's. An impostor can register a name;
///         it cannot make cleveland.gov vouch for its key.
///
///         The registry binds nothing by itself. The binding is in the trust:
///         `OpenHouseTrust.authority` names one of these keys, and from that
///         moment the government's servers hold the civic pause and the
///         foreclosure hold. Endorsements here are the other direction — a
///         government pointing at deals it watches — so "adopted by the city"
///         is a claim either side can make and anyone can check both halves of.
contract CivicRegistry {
    struct City {
        address key;           // the government's signing key — its servers act as this
        string  name;          // "Cleveland Housing Authority"
        string  region;        // ISO 3166-2, e.g. "US-OH" — the jurisdiction claimed
        string  uri;           // the civic server it runs: https://housing.cleveland.gov
        uint64  registeredAt;
        uint64  updatedAt;
        bool    active;        // the government's own switch, nobody else's
    }

    mapping(address => City) public cities;
    address[] public roster;

    /// city key => trust => endorsed. A government saying, on the record, that
    /// its servers watch this deal.
    mapping(address => mapping(address => bool)) public isEndorsed;
    mapping(address => address[]) private _endorsedBy;   // city  => trusts
    mapping(address => address[]) private _endorsersOf;  // trust => cities

    event CityRegistered(address indexed key, string name, string region, string uri);
    event CityUpdated(address indexed key, string name, string region, string uri);
    event CityActiveSet(address indexed key, bool active);
    event Endorsed(address indexed city, address indexed trust, string note);
    event Revoked(address indexed city, address indexed trust, string reason);

    modifier registered() {
        require(cities[msg.sender].key != address(0), "Civic: not registered");
        _;
    }

    /// @notice A government registers itself. The caller IS the key — a city
    ///         signs up from the same servers it will verify and override from.
    function register(string calldata name, string calldata region, string calldata uri) external {
        require(cities[msg.sender].key == address(0), "Civic: already registered");
        require(bytes(name).length > 0, "Civic: no name");
        cities[msg.sender] = City({
            key: msg.sender,
            name: name,
            region: region,
            uri: uri,
            registeredAt: uint64(block.timestamp),
            updatedAt: uint64(block.timestamp),
            active: true
        });
        roster.push(msg.sender);
        emit CityRegistered(msg.sender, name, region, uri);
    }

    /// @notice Update the listing — a renamed department, a moved server.
    function update(string calldata name, string calldata region, string calldata uri)
        external registered
    {
        require(bytes(name).length > 0, "Civic: no name");
        City storage c = cities[msg.sender];
        c.name = name;
        c.region = region;
        c.uri = uri;
        c.updatedAt = uint64(block.timestamp);
        emit CityUpdated(msg.sender, name, region, uri);
    }

    /// @notice A government switches its own listing off (or back on). The
    ///         record stays — a directory that forgets is not a record — but
    ///         an inactive city is one whose servers stopped answering for it.
    function setActive(bool active) external registered {
        cities[msg.sender].active = active;
        cities[msg.sender].updatedAt = uint64(block.timestamp);
        emit CityActiveSet(msg.sender, active);
    }

    /// @notice The government puts a deal under its watch, publicly. This is
    ///         how "adopted by the city" becomes checkable rather than said:
    ///         the endorsement here, the `authority` seat in the trust, and
    ///         the key on the city's own domain all have to agree.
    function endorse(address trust, string calldata note) external registered {
        require(trust != address(0), "Civic: zero trust");
        require(!isEndorsed[msg.sender][trust], "Civic: already endorsed");
        isEndorsed[msg.sender][trust] = true;
        _endorsedBy[msg.sender].push(trust);
        _endorsersOf[trust].push(msg.sender);
        emit Endorsed(msg.sender, trust, note);
    }

    /// @notice The government withdraws its watch, with the reason on the
    ///         record. The lists keep the address; `isEndorsed` goes false —
    ///         history is not rewritten, standing is.
    function revoke(address trust, string calldata reason) external registered {
        require(isEndorsed[msg.sender][trust], "Civic: not endorsed");
        isEndorsed[msg.sender][trust] = false;
        emit Revoked(msg.sender, trust, reason);
    }

    // ─────────────────────────────────────────── Views ─────

    function cityCount() external view returns (uint256) { return roster.length; }

    function cityAt(uint256 i) external view returns (City memory) {
        return cities[roster[i]];
    }

    function cityOf(address key) external view returns (City memory) {
        return cities[key];
    }

    function isCity(address key) external view returns (bool) {
        return cities[key].key != address(0) && cities[key].active;
    }

    /// @notice Every trust this government has ever endorsed. Check each
    ///         address against `isEndorsed` for current standing.
    function endorsedBy(address city) external view returns (address[] memory) {
        return _endorsedBy[city];
    }

    /// @notice Every government that has ever endorsed this trust. Same rule:
    ///         the list is history, `isEndorsed` is standing.
    function endorsersOf(address trust) external view returns (address[] memory) {
        return _endorsersOf[trust];
    }

    /// @notice Governments currently standing behind a trust.
    function standingEndorsers(address trust) external view returns (address[] memory out) {
        address[] storage all = _endorsersOf[trust];
        uint256 n;
        for (uint256 i = 0; i < all.length; i++) {
            if (isEndorsed[all[i]][trust] && cities[all[i]].active) n++;
        }
        out = new address[](n);
        uint256 j;
        for (uint256 i = 0; i < all.length; i++) {
            if (isEndorsed[all[i]][trust] && cities[all[i]].active) out[j++] = all[i];
        }
    }
}
