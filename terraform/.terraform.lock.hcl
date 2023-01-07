# This file is maintained automatically by "terraform init".
# Manual edits may be lost in future updates.

provider "registry.terraform.io/fluxcd/flux" {
  version     = "0.22.2"
  constraints = ">= 0.22.2"
  hashes = [
    "h1:jyWIOo6Eh9DZFi/78eUzHy08H5fb7QBqmQ4CxNN5jro=",
    "zh:144d9257f94d59b1326a964617631a294e0f3fb8ac4da54d47ba09cb4e48156d",
    "zh:3c1a6834a8e982f3ff20ca43f3256a04bcca5c4afe76f235a057f244bb68767d",
    "zh:4065bccdee5b8b299895e5e00f6073cd89424ea726970f4eafd900636218ae8f",
    "zh:4c8fc5a4e3cc865c7e266b2ae81fa34f45d18e7f78f522c9b14e5cfd88353a59",
    "zh:4f75cae15b27b2900ba1a9874936bfafbb142e5fbc2952627e13ae349bc55dcf",
    "zh:59743f5d51ff91793eda5542c4e239e45a9e5d078f4fecaa3e06f0bf7cdb8e1a",
    "zh:7226b9d759adace78fd94467be9c366daf05af8a70b08f026e3ef1714f1f7a99",
    "zh:7ede86b0e5b13519a78cda41f8f46a03b663d089e14c92d9442332f8f66a538a",
    "zh:7ef3f55a943f6bc8d80ddf9ecc89bcf7baef7b0443f9984097d08f215c907b61",
    "zh:8163ec3eb038f9bc7ee33b889f3f8298e3cdd7f539efb7c24493fe2eb61c5e4c",
    "zh:bdc6e0fa5fcb9d824f7f02e35e52b6e8ad3977b34b7a6527de9ea73edff9bbe4",
    "zh:be8324deb48b16968f0b42838da413d1b5751a9804f142e5a402d5d4d6a67c9b",
    "zh:c8092a8ba68c674af895ad09cc9dfdbb36acef5bd6eef73c3adfc902fd5baa0b",
    "zh:e220be8b9433ce5b53e05b9913020d5be562d92b0e2e1509f6fcd1b0d8edf99b",
  ]
}

provider "registry.terraform.io/gavinbunney/kubectl" {
  version     = "1.14.0"
  constraints = ">= 1.10.0"
  hashes = [
    "h1:gLFn+RvP37sVzp9qnFCwngRjjFV649r6apjxvJ1E/SE=",
    "zh:0350f3122ff711984bbc36f6093c1fe19043173fad5a904bce27f86afe3cc858",
    "zh:07ca36c7aa7533e8325b38232c77c04d6ef1081cb0bac9d56e8ccd51f12f2030",
    "zh:0c351afd91d9e994a71fe64bbd1662d0024006b3493bb61d46c23ea3e42a7cf5",
    "zh:39f1a0aa1d589a7e815b62b5aa11041040903b061672c4cfc7de38622866cbc4",
    "zh:428d3a321043b78e23c91a8d641f2d08d6b97f74c195c654f04d2c455e017de5",
    "zh:4baf5b1de2dfe9968cc0f57fd4be5a741deb5b34ee0989519267697af5f3eee5",
    "zh:6131a927f9dffa014ab5ca5364ac965fe9b19830d2bbf916a5b2865b956fdfcf",
    "zh:c62e0c9fd052cbf68c5c2612af4f6408c61c7e37b615dc347918d2442dd05e93",
    "zh:f0beffd7ce78f49ead612e4b1aefb7cb6a461d040428f514f4f9cc4e5698ac65",
  ]
}

provider "registry.terraform.io/hashicorp/kubernetes" {
  version     = "2.16.1"
  constraints = ">= 2.0.2"
  hashes = [
    "h1:i+DwtJK82sIWmTcQA9lL0mlET+14/QpUqv10fU2o3As=",
    "zh:06224975f5910d41e73b35a4d5079861da2c24f9353e3ebb015fbb3b3b996b1c",
    "zh:2bc400a8d9fe7755cca27c2551564a9e2609cfadc77f526ef855114ee02d446f",
    "zh:3a479014187af1d0aec3a1d3d9c09551b801956fe6dd29af1186dec86712731b",
    "zh:73fb0a69f1abdb02858b6589f7fab6d989a0f422f7ad95ed662aaa84872d3473",
    "zh:a33852cd382cbc8e06d3f6c018b468ad809d24d912d64722e037aed1f9bf39db",
    "zh:b533ff2214dca90296b1d22eace7eaa7e3efe5a7ae9da66a112094abc932db4f",
    "zh:ddf74d8bb1aeb01dc2c36ef40e2b283d32b2a96db73f6daaf179fa2f10949c80",
    "zh:e720f3a15d34e795fa9ff90bc755e838ebb4aef894aa2a423fb16dfa6d6b0667",
    "zh:e789ae70a658800cb0a19ef7e4e9b26b5a38a92b43d1f41d64fc8bb46539cefb",
    "zh:e8aed7dc0bd8f843d607dee5f72640dbef6835a8b1c6ea12cea5b4ec53e463f7",
    "zh:f569b65999264a9416862bca5cd2a6177d94ccb0424f3a4ef424428912b9cb3c",
    "zh:fb3ac4f43c8b0dfc0b0103dd0f062ea72b3a34518d4c8808e3a44c9a3dd5f024",
  ]
}

provider "registry.terraform.io/hashicorp/local" {
  version = "2.2.3"
  hashes = [
    "h1:aWp5iSUxBGgPv1UnV5yag9Pb0N+U1I0sZb38AXBFO8A=",
    "zh:04f0978bb3e052707b8e82e46780c371ac1c66b689b4a23bbc2f58865ab7d5c0",
    "zh:6484f1b3e9e3771eb7cc8e8bab8b35f939a55d550b3f4fb2ab141a24269ee6aa",
    "zh:78a56d59a013cb0f7eb1c92815d6eb5cf07f8b5f0ae20b96d049e73db915b238",
    "zh:78d5eefdd9e494defcb3c68d282b8f96630502cac21d1ea161f53cfe9bb483b3",
    "zh:8aa9950f4c4db37239bcb62e19910c49e47043f6c8587e5b0396619923657797",
    "zh:996beea85f9084a725ff0e6473a4594deb5266727c5f56e9c1c7c62ded6addbb",
    "zh:9a7ef7a21f48fabfd145b2e2a4240ca57517ad155017e86a30860d7c0c109de3",
    "zh:a63e70ac052aa25120113bcddd50c1f3cfe61f681a93a50cea5595a4b2cc3e1c",
    "zh:a6e8d46f94108e049ad85dbed60354236dc0b9b5ec8eabe01c4580280a43d3b8",
    "zh:bb112ce7efbfcfa0e65ed97fa245ef348e0fd5bfa5a7e4ab2091a9bd469f0a9e",
    "zh:d7bec0da5c094c6955efed100f3fe22fca8866859f87c025be1760feb174d6d9",
    "zh:fb9f271b72094d07cef8154cd3d50e9aa818a0ea39130bc193132ad7b23076fd",
  ]
}

provider "registry.terraform.io/hashicorp/tls" {
  version     = "3.1.0"
  constraints = "3.1.0"
  hashes = [
    "h1:fUJX8Zxx38e2kBln+zWr1Tl41X+OuiE++REjrEyiOM4=",
    "zh:3d46616b41fea215566f4a957b6d3a1aa43f1f75c26776d72a98bdba79439db6",
    "zh:623a203817a6dafa86f1b4141b645159e07ec418c82fe40acd4d2a27543cbaa2",
    "zh:668217e78b210a6572e7b0ecb4134a6781cc4d738f4f5d09eb756085b082592e",
    "zh:95354df03710691773c8f50a32e31fca25f124b7f3d6078265fdf3c4e1384dca",
    "zh:9f97ab190380430d57392303e3f36f4f7835c74ea83276baa98d6b9a997c3698",
    "zh:a16f0bab665f8d933e95ca055b9c8d5707f1a0dd8c8ecca6c13091f40dc1e99d",
    "zh:be274d5008c24dc0d6540c19e22dbb31ee6bfdd0b2cddd4d97f3cd8a8d657841",
    "zh:d5faa9dce0a5fc9d26b2463cea5be35f8586ab75030e7fa4d4920cd73ee26989",
    "zh:e9b672210b7fb410780e7b429975adcc76dd557738ecc7c890ea18942eb321a5",
    "zh:eb1f8368573d2370605d6dbf60f9aaa5b64e55741d96b5fb026dbfe91de67c0d",
    "zh:fc1e12b713837b85daf6c3bb703d7795eaf1c5177aebae1afcf811dd7009f4b0",
  ]
}

provider "registry.terraform.io/integrations/github" {
  version     = "5.13.0"
  constraints = ">= 4.5.2"
  hashes = [
    "h1:h5m2rgps7szJcS58Qw8zVIA0sbzZy0zJz/6brTMKxMk=",
    "zh:037bb9526d87b06b8077ebc1965b6705a8f5cb1c1eb7289a292ab37e90ba825d",
    "zh:25c21880806817cd3090797504843b5052f935024935e2b20103e0b89e610ba3",
    "zh:4ecc4a8601edbc776ed9ae08ea9d52613d03302a2120594ac1a700c7688bb690",
    "zh:64383aa53d7526e18715fd91606fdeaa02660a954d6a1dd32828ecb91aa01074",
    "zh:7372aac68fe118d4ada9f8fd7c8fbb0bfac4e0ae3493be38adba6925c5954187",
    "zh:89fd0c8396cc2a20c1a9c22d94355e8d50ae99032cace6dbcad2d15fff169f01",
    "zh:8a9b4a084118eb5948724324c703cc09ec55bdb7a707b9313ccdd4d081b142e0",
    "zh:8d2b01b731540f43d0cfd62cd5eb0a32219a87bb9b0dc7354612385a74ebc9c2",
    "zh:c0c6253f14c5376f0cc0eeaf72ceab203fd09cdc0a4e4986ad4fac5a2eef1846",
    "zh:c2b964b360a302ee8268c75cbe3abc8fe656eb6ba4dc371f689eab64c422ab30",
    "zh:c31cfb09a639c9d08be6aeb5e996bb12da58e51d0fe8c879cea587d5ba2773a6",
    "zh:cee1d8178bee501279166d6bed38067beac22e976567b2ab7da84913689a2856",
    "zh:d0073cdd4d63e79550b93e5009280d9dd06fa7f16abe9faddb9dff79e9035e12",
    "zh:e481d0366550f33e930a4f031c779651474102a373f916adc6dcdb2f2aceacef",
  ]
}

provider "registry.terraform.io/siderolabs/talos" {
  version     = "0.1.0-alpha.10"
  constraints = "0.1.0-alpha.10"
  hashes = [
    "h1:9lezXo4pKvynGMVmSsBAh7ovUwx0EuCaanm1yutIYlY=",
    "zh:0fa82a384b25a58b65523e0ea4768fa1212b1f5cfc0c9379d31162454fedcc9d",
    "zh:135b7198d14943068c99c321ff1ddce01faec6233bd5fadea4ff9e43daf8b25e",
    "zh:1e6c4ca55c8a5314a6dfd3ac6626773c063af717650744b835be5745aac5a432",
    "zh:3240a0a68e5458f3525324683536fce5574286ea873837169e8cc32c474cf4aa",
    "zh:40fa13271133c9f994e542768ff2d51d7e9225a8eaa26710900c7a3008b86b3b",
    "zh:6c11cf3e0344899fdd1282a7f1e3236f35c2fa1a9a1e0454dac6ae5767579a68",
    "zh:78fb914d29569363887bcd5eae000c52beb851d11be40019956c1aef3711f5af",
    "zh:7c241a4b82709b0fd4e65c2b1bc9cd4081edc3ef0c727e04eaba28a2179ce748",
    "zh:807c247cad1f7397995436e0139f309d5f9ee88a9a1a20e958ecbaba926a21a8",
    "zh:8a69ef309ef2e61b8128eb39f1dc1335f6951f504b996d754179013e93cc0cf1",
    "zh:a8af1837862ec143a7b65e55a1200eda7f05d2d8ddc0151955fb92ba57a8bddf",
    "zh:b5b896f51988aaad234284f7cc76e7a9da19ebba835c62269b3d99224c63b840",
    "zh:d29c2eaae854f60aa3f1ed7178fa4c4db3b0549fad1f6b477546be06a13defd7",
    "zh:dd689b433736e71758fc34202cdea722ec91cd85a5c51fe4bb2501dac8c48e51",
    "zh:eea3851d02606f5b56fa5545c1fe8c48bb153a7a0db73bd277cc1dd704cea7e8",
  ]
}

provider "registry.terraform.io/telmate/proxmox" {
  version     = "2.9.11"
  constraints = ">= 2.9.10"
  hashes = [
    "h1:RKM2pvHNJrQKcMD7omaPiM099vWGgDnnZqn1kGknYXU=",
    "zh:0db1e3940cf208e56919e68c6d557dfc87d380316a474c8999916308bf991440",
    "zh:2a0ae7af5b2f96d53b24f34575bc72ccbb79cab870901f26f00a301613f7c69e",
    "zh:2f9eb4a4d2c5db04ec0940d7e250aaf1bac559acc787a5883688ba42159f8b8e",
    "zh:362a5b44995a51c8de78f0106aa7741f212bb15fbf2d7477794ea3ee63e2c17d",
    "zh:4d212404b741848cef1e469e390ad1df659bbfa8d47cd079d82d83c288925438",
    "zh:54a65a01946839db263f8da389791863f6909db9d5fcfdb472e23b14883a5b6c",
    "zh:5dfc95303efc53686b23762dfa4c50d887eb4cc0a3e9d527adc29b3a9f0439eb",
    "zh:68db84c007cbdd7267d1f7b767b0b2b91e9ee2e2b92ac1d8a1568f3bc61e67cd",
    "zh:85d45466445883ae64eed3d5fcb996de389ecf9268f0f7d2f22911fb3f56a344",
    "zh:8673f8c794ea8413dc9a3933902492b3e5be99e79bc611fcef415be7d7268210",
    "zh:d5041f72f550f3c81dafecb4e7dfca9f849737154a0e2c81434df6c72d75af25",
    "zh:e60e03b495dd76660784a8ab07d8db0ce1df7165e713efb350c1864d92f87a8c",
    "zh:ed1f75a2fe7d764356119a590f301ab8fd40cfeea78a514450868beb92115f28",
    "zh:efa4140b78775509665370c915e60c9043a1325d608f96da151f8f7fcc7cb45e",
  ]
}
