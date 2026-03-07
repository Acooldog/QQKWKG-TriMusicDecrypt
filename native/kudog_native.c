#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define KUDOG_EXPORT __declspec(dllexport)
#else
#define KUDOG_EXPORT
#endif

static const uint8_t PUB_KEY_MEND[] = {
    0xB8, 0xD5, 0x3D, 0xB2, 0xE9, 0xAF, 0x78, 0x8C, 0x83, 0x33, 0x71, 0x51, 0x76, 0xA0,
    0xCD, 0x37, 0x2F, 0x3E, 0x35, 0x8D, 0xA9, 0xBE, 0x98, 0xB7, 0xE7, 0x8C, 0x22, 0xCE,
    0x5A, 0x61, 0xDF, 0x68, 0x69, 0x89, 0xFE, 0xA5, 0xB6, 0xDE, 0xA9, 0x77, 0xFC, 0xC8,
    0xBD, 0xBD, 0xE5, 0x6D, 0x3E, 0x5A, 0x36, 0xEF, 0x69, 0x4E, 0xBE, 0xE1, 0xE9, 0x66,
    0x1C, 0xF3, 0xD9, 0x02, 0xB6, 0xF2, 0x12, 0x9B, 0x44, 0xD0, 0x6F, 0xB9, 0x35, 0x89,
    0xB6, 0x46, 0x6D, 0x73, 0x82, 0x06, 0x69, 0xC1, 0xED, 0xD7, 0x85, 0xC2, 0x30, 0xDF,
    0xA2, 0x62, 0xBE, 0x79, 0x2D, 0x62, 0x62, 0x3D, 0x0D, 0x7E, 0xBE, 0x48, 0x89, 0x23,
    0x02, 0xA0, 0xE4, 0xD5, 0x75, 0x51, 0x32, 0x02, 0x53, 0xFD, 0x16, 0x3A, 0x21, 0x3B,
    0x16, 0x0F, 0xC3, 0xB2, 0xBB, 0xB3, 0xE2, 0xBA, 0x3A, 0x3D, 0x13, 0xEC, 0xF6, 0x01,
    0x45, 0x84, 0xA5, 0x70, 0x0F, 0x93, 0x49, 0x0C, 0x64, 0xCD, 0x31, 0xD5, 0xCC, 0x4C,
    0x07, 0x01, 0x9E, 0x00, 0x1A, 0x23, 0x90, 0xBF, 0x88, 0x1E, 0x3B, 0xAB, 0xA6, 0x3E,
    0xC4, 0x73, 0x47, 0x10, 0x7E, 0x3B, 0x5E, 0xBC, 0xE3, 0x00, 0x84, 0xFF, 0x09, 0xD4,
    0xE0, 0x89, 0x0F, 0x5B, 0x58, 0x70, 0x4F, 0xFB, 0x65, 0xD8, 0x5C, 0x53, 0x1B, 0xD3,
    0xC8, 0xC6, 0xBF, 0xEF, 0x98, 0xB0, 0x50, 0x4F, 0x0F, 0xEA, 0xE5, 0x83, 0x58, 0x8C,
    0x28, 0x2C, 0x84, 0x67, 0xCD, 0xD0, 0x9E, 0x47, 0xDB, 0x27, 0x50, 0xCA, 0xF4, 0x63,
    0x63, 0xE8, 0x97, 0x7F, 0x1B, 0x4B, 0x0C, 0xC2, 0xC1, 0x21, 0x4C, 0xCC, 0x58, 0xF5,
    0x94, 0x52, 0xA3, 0xF3, 0xD3, 0xE0, 0x68, 0xF4, 0x00, 0x23, 0xF3, 0x5E, 0x0A, 0x7B,
    0x93, 0xDD, 0xAB, 0x12, 0xB2, 0x13, 0xE8, 0x84, 0xD7, 0xA7, 0x9F, 0x0F, 0x32, 0x4C,
    0x55, 0x1D, 0x04, 0x36, 0x52, 0xDC, 0x03, 0xF3, 0xF9, 0x4E, 0x42, 0xE9, 0x3D, 0x61,
    0xEF, 0x7C, 0xB6, 0xB3, 0x93, 0x50,
};

static uint8_t rotate_byte(uint8_t value, uint8_t bits) {
    uint8_t shift = (uint8_t)((bits + 4U) & 0x07U);
    return (uint8_t)(((value << shift) & 0xFFU) | (value >> shift));
}

KUDOG_EXPORT int kudog_decode_v3(
    uint8_t *data,
    size_t data_len,
    const uint8_t *own_key,
    size_t own_key_len,
    const uint8_t *pub_key,
    size_t pub_key_len,
    uint64_t start_pos
) {
    const size_t mend_len = sizeof(PUB_KEY_MEND) / sizeof(PUB_KEY_MEND[0]);
    size_t index;
    if (data == NULL || own_key == NULL || pub_key == NULL || own_key_len == 0U) {
        return 1;
    }
    for (index = 0; index < data_len; ++index) {
        uint64_t pos = start_pos + (uint64_t)index;
        uint64_t pub_index = pos >> 4U;
        uint8_t value;
        if (pub_index >= (uint64_t)pub_key_len) {
            return 2;
        }
        value = (uint8_t)(data[index] ^ own_key[(size_t)(pos % (uint64_t)own_key_len)]);
        value = (uint8_t)(value ^ ((value & 0x0FU) << 4U));
        {
            uint8_t pub_value = (uint8_t)(PUB_KEY_MEND[(size_t)(pos % (uint64_t)mend_len)] ^ pub_key[(size_t)pub_index]);
            pub_value = (uint8_t)(pub_value ^ ((pub_value & 0x0FU) << 4U));
            value = (uint8_t)(value ^ pub_value);
        }
        data[index] = value;
    }
    return 0;
}

KUDOG_EXPORT int kudog_qmc_map_decrypt(
    uint8_t *data,
    size_t data_len,
    const uint8_t *key,
    size_t key_len,
    uint64_t start_pos
) {
    size_t index;
    if (data == NULL || key == NULL || key_len == 0U) {
        return 1;
    }
    for (index = 0; index < data_len; ++index) {
        uint64_t pos = start_pos + (uint64_t)index;
        uint64_t idx = ((pos * pos) + 71214ULL) % (uint64_t)key_len;
        if (pos > 0x7FFFULL) {
            pos %= 0x7FFFULL;
            idx = ((pos * pos) + 71214ULL) % (uint64_t)key_len;
        }
        data[index] ^= rotate_byte(key[(size_t)idx], (uint8_t)(idx & 0x07U));
    }
    return 0;
}

static uint32_t build_hash_base(const uint8_t *key, size_t key_len) {
    uint32_t hash_base = 1U;
    size_t index;
    for (index = 0; index < key_len; ++index) {
        uint8_t value = key[index];
        uint32_t next_hash;
        if (value == 0U) {
            continue;
        }
        next_hash = hash_base * (uint32_t)value;
        if (next_hash == 0U || next_hash <= hash_base) {
            break;
        }
        hash_base = next_hash;
    }
    return hash_base;
}

static size_t get_segment_skip(const uint8_t *key, size_t key_len, uint32_t hash_base, uint64_t idx) {
    uint8_t seed = key[(size_t)(idx % (uint64_t)key_len)];
    double ratio = ((double)hash_base / (double)((idx + 1ULL) * (uint64_t)seed)) * 100.0;
    return ((size_t)ratio) % key_len;
}

static void decrypt_first_segment(
    uint8_t *data,
    size_t data_len,
    const uint8_t *key,
    size_t key_len,
    uint32_t hash_base,
    uint64_t offset
) {
    size_t index;
    for (index = 0; index < data_len; ++index) {
        data[index] ^= key[get_segment_skip(key, key_len, hash_base, offset + (uint64_t)index)];
    }
}

static void decrypt_segment(
    uint8_t *data,
    size_t data_len,
    const uint8_t *key,
    size_t key_len,
    const uint8_t *base_box,
    uint32_t hash_base,
    uint64_t offset
) {
    size_t skip = (size_t)(offset % 5120ULL) + get_segment_skip(key, key_len, hash_base, offset / 5120ULL);
    uint8_t *box = (uint8_t *)malloc(key_len);
    size_t index;
    size_t j = 0U;
    size_t k = 0U;
    intptr_t stream_index;
    if (box == NULL) {
        return;
    }
    memcpy(box, base_box, key_len);
    for (stream_index = -(intptr_t)skip; stream_index < (intptr_t)data_len; ++stream_index) {
        j = (j + 1U) % key_len;
        k = (box[j] + k) % key_len;
        {
            uint8_t tmp = box[j];
            box[j] = box[k];
            box[k] = tmp;
        }
        if (stream_index >= 0) {
            index = (size_t)stream_index;
            data[index] ^= box[(box[j] + box[k]) % key_len];
        }
    }
    free(box);
}

KUDOG_EXPORT int kudog_qmc_rc4_decrypt(
    uint8_t *data,
    size_t data_len,
    const uint8_t *key,
    size_t key_len,
    uint64_t start_pos
) {
    uint8_t *base_box;
    uint32_t hash_base;
    size_t index;
    size_t processed = 0U;
    uint64_t offset = start_pos;
    size_t remaining = data_len;
    if (data == NULL || key == NULL || key_len == 0U) {
        return 1;
    }
    base_box = (uint8_t *)malloc(key_len);
    if (base_box == NULL) {
        return 3;
    }
    for (index = 0; index < key_len; ++index) {
        base_box[index] = (uint8_t)(index & 0xFFU);
    }
    {
        size_t j = 0U;
        for (index = 0; index < key_len; ++index) {
            j = (j + base_box[index] + key[index % key_len]) % key_len;
            {
                uint8_t tmp = base_box[index];
                base_box[index] = base_box[j];
                base_box[j] = tmp;
            }
        }
    }
    hash_base = build_hash_base(key, key_len);

    if (offset < 128ULL) {
        size_t block = remaining;
        if (block > (size_t)(128ULL - offset)) {
            block = (size_t)(128ULL - offset);
        }
        decrypt_first_segment(data, block, key, key_len, hash_base, offset);
        offset += (uint64_t)block;
        processed += block;
        remaining -= block;
    }

    if (remaining > 0U && (offset % 5120ULL) != 0ULL) {
        size_t block = remaining;
        size_t boundary = (size_t)(5120ULL - (offset % 5120ULL));
        if (block > boundary) {
            block = boundary;
        }
        decrypt_segment(data + processed, block, key, key_len, base_box, hash_base, offset);
        offset += (uint64_t)block;
        processed += block;
        remaining -= block;
    }

    while (remaining > 5120U) {
        decrypt_segment(data + processed, 5120U, key, key_len, base_box, hash_base, offset);
        offset += 5120ULL;
        processed += 5120U;
        remaining -= 5120U;
    }

    if (remaining > 0U) {
        decrypt_segment(data + processed, remaining, key, key_len, base_box, hash_base, offset);
    }

    free(base_box);
    return 0;
}
