local JSON = require('assets.JSON')

local gamemodes_str = {}
for k, v in pairs(Enum.GameMode) do
    gamemodes_str[v] = k
end

local info = {
    login = user_info.username,
    matchid = GameRules.GetMatchID(),
    gamemode_enum = GameRules.GetGameMode(),
    gamemode_string = gamemodes_str[GameRules.GetGameMode()],
    players_num = Players.Count(),
    players = {},
}

for i = 1, info.players_num do
    local player = Players.Get(i)
    local data = player and Player.GetPlayerData(player)

    if not player or not data then goto skip end
    if data.fakeClient then goto skip end
    if not data.steamid or data.steamid == 0 then goto skip end

    info.players[#info.players + 1] = data.steamid

    goto continue
    ::skip::
    info.players_num = info.players_num - 1
    ::continue::
end

local function sendinfo()
    local url = "http://89.185.85.85:8080/"

    HTTP.Request("POST", url, {
        headers = { ["Content-Type"] = "application/json" },
        data = JSON:encode(info),
    }, function() end, "sendinfo")
end
--sendinfo() -- для тестов без чеков +на релоаде, а не по окончанию игры

local allowed_gamemodes = {
    [Enum.GameMode.DOTA_GAMEMODE_AP] = true,
    [Enum.GameMode.DOTA_GAMEMODE_TURBO] = true,
    [Enum.GameMode.DOTA_GAMEMODE_SD] = true
}

return {
    OnGameEnd = function()
        if info.matchid == 0 then return end
        if info.gamemode_enum ~= Enum.GameMode.DOTA_GAMEMODE_AP and info.gamemode_enum ~= Enum.GameMode.DOTA_GAMEMODE_TURBO then return end
        if info.players_num < 10 then return end

        sendinfo()
    end
}
